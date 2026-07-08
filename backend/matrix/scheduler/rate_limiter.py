"""限速器组合：活跃窗 + 令牌桶 + 日上限 + 抖动。

按 mcp-tools-notes.md §1：
- 单设备日发布 ≤ 5 / 互动 ≤ 30
- 单账号日发布 ≤ 3 / 互动 ≤ 20
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Awaitable, Callable, Protocol

from .active_window import is_in_active_window
from .circuit_breaker import CircuitBreaker
from .jitter import jitter_delay
from .token_bucket import RateLimitTimeout, TokenBucket


PUBLISH_ACTIONS = {"device_publish"}
INTERACT_ACTIONS = {
    "device_interact",
    "device_like",
    "device_comment",
    "device_collect",
    "device_follow",
}


@dataclass
class RateLimitDecision:
    ok: bool
    reason: str = ""
    jitter_seconds: float = 0.0


class TaskLike(Protocol):
    """Task 鸭子类型，避免调度器反向依赖 db 模型。"""

    id: object
    account_id: object
    device_id: object
    action: str
    scheduled_at: datetime


@dataclass
class _DailyCounter:
    """单 (scope, key) 的按日计数器。"""

    counts: dict[date, dict[str, int]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))

    def add(self, scope: str, key: object, kind: str, day: date) -> int:
        bucket = self.counts[day]
        bucket[(scope, str(key), kind)] = bucket[(scope, str(key), kind)] + 1
        return bucket[(scope, str(key), kind)]

    def get(self, scope: str, key: object, kind: str, day: date) -> int:
        return self.counts[day].get((scope, str(key), kind), 0)


class RateLimiter:
    """组合限速器：活跃窗 → 日上限 → 令牌桶 → 抖动。"""

    def __init__(
        self,
        *,
        bucket_capacity: int = 30,
        bucket_refill_rate: float = 1 / 30,
        device_publish_per_day: int = 5,
        device_interact_per_day: int = 30,
        account_publish_per_day: int = 3,
        account_interact_per_day: int = 20,
        jitter_base: float = 1.0,
        jitter_sigma: float = 0.5,
        breaker: CircuitBreaker | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._buckets: dict[object, TokenBucket] = {}
        self._bucket_lock = asyncio.Lock()
        self._daily = _DailyCounter()
        self._daily_lock = asyncio.Lock()

        self.bucket_capacity = bucket_capacity
        self.bucket_refill_rate = bucket_refill_rate
        self.device_publish_per_day = device_publish_per_day
        self.device_interact_per_day = device_interact_per_day
        self.account_publish_per_day = account_publish_per_day
        self.account_interact_per_day = account_interact_per_day
        self.jitter_base = jitter_base
        self.jitter_sigma = jitter_sigma
        self.breaker = breaker or CircuitBreaker()
        self._clock = clock

    async def _get_bucket(self, account_id: object) -> TokenBucket:
        async with self._bucket_lock:
            bucket = self._buckets.get(account_id)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=self.bucket_capacity,
                    refill_rate=self.bucket_refill_rate,
                )
                self._buckets[account_id] = bucket
            return bucket

    def _kind(self, action: str) -> str:
        if action in PUBLISH_ACTIONS:
            return "publish"
        if action in INTERACT_ACTIONS:
            return "interact"
        return "other"

    def _daily_cap(self, scope: str, kind: str) -> int:
        if scope == "device" and kind == "publish":
            return self.device_publish_per_day
        if scope == "device" and kind == "interact":
            return self.device_interact_per_day
        if scope == "account" and kind == "publish":
            return self.account_publish_per_day
        if scope == "account" and kind == "interact":
            return self.account_interact_per_day
        return 10**9  # 兜底：未分类不限

    async def check_daily(self, task: TaskLike) -> RateLimitDecision:
        """仅做日上限检查（活跃窗之外不应调用本方法）。"""
        kind = self._kind(task.action)
        day = self._clock().date()
        async with self._daily_lock:
            for scope, key in (("device", task.device_id), ("account", task.account_id)):
                cap = self._daily_cap(scope, kind)
                used = self._daily.get(scope, key, kind, day)
                if used >= cap:
                    return RateLimitDecision(ok=False, reason=f"daily_cap_{scope}_{kind}")
        return RateLimitDecision(ok=True)

    async def record(self, task: TaskLike) -> None:
        """操作成功后记一次计数。"""
        kind = self._kind(task.action)
        day = self._clock().date()
        async with self._daily_lock:
            for scope, key in (("device", task.device_id), ("account", task.account_id)):
                self._daily.add(scope, key, kind, day)

    async def throttle(self, task: TaskLike, persona_config: dict | None = None) -> RateLimitDecision:
        """执行前完整检查：活跃窗 → 日上限 → 熔断 → 令牌桶 → 抖动延迟。

        返回 :class:`RateLimitDecision`，调用方在 ``ok=False`` 时应排队等待。
        ``jitter_seconds`` 是建议下发前 sleep 的秒数（已注入抖动）。
        """
        now = self._clock()

        # 1. 活跃窗
        if not is_in_active_window(now, persona_config):
            return RateLimitDecision(ok=False, reason="out_of_active_window")

        # 2. 熔断
        if self.breaker.is_open():
            return RateLimitDecision(ok=False, reason="circuit_open")

        # 3. 日上限
        daily = await self.check_daily(task)
        if not daily.ok:
            return daily

        # 4. 令牌桶（等令牌，可能抛 RateLimitTimeout）
        bucket = await self._get_bucket(task.account_id)
        try:
            await bucket.acquire(timeout=600)
        except RateLimitTimeout:
            return RateLimitDecision(ok=False, reason="rate_limit_timeout")

        # 5. 抖动（成功路径返回 sleep 时长）
        jitter = jitter_delay(self.jitter_base, self.jitter_sigma)
        if jitter > 0:
            await asyncio.sleep(jitter)
        return RateLimitDecision(ok=True, jitter_seconds=jitter)

    async def execute(
        self,
        task: TaskLike,
        runner: Callable[[TaskLike], Awaitable[bool]],
        persona_config: dict | None = None,
    ) -> RateLimitDecision:
        """完整执行：throttle → 跑任务 → 成功/失败记录。"""
        decision = await self.throttle(task, persona_config)
        if not decision.ok:
            return decision

        try:
            ok = await runner(task)
        except Exception:
            self.breaker.record_failure()
            return RateLimitDecision(ok=False, reason="runner_error")

        if ok:
            await self.record(task)
            return RateLimitDecision(ok=True, jitter_seconds=decision.jitter_seconds)

        self.breaker.record_failure()
        return RateLimitDecision(ok=False, reason="runner_returned_false")
