"""matrix.scheduler 单元测试。

覆盖：
- TokenBucket: capacity / refill / timeout
- CircuitBreaker: threshold / cool_off
- jitter: 分布合理性
- RateLimiter: throttle 行为
- Scheduler: mock loader / writer / executor 的 dispatch
- active_window: 边界时间
"""
from __future__ import annotations

import asyncio
import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from matrix.scheduler import (
    CircuitBreaker,
    RateLimiter,
    Scheduler,
    TaskExecutor,
    TaskLike,
    TaskLoader,
    TaskStatusWriter,
    TokenBucket,
    is_in_active_window,
    jitter_delay,
)
from matrix.scheduler.token_bucket import RateLimitTimeout


@dataclass
class _FakeTask:
    """结构上满足 TaskLike Protocol 的最小可序列化对象。"""

    id: UUID
    plan_id: UUID
    device_id: UUID
    account_id: UUID
    action: str
    payload: dict = field(default_factory=dict)
    request_id: str = ""
    status: str = "pending"
    attempts: int = 0
    last_error: dict | None = None
    scheduled_at: datetime | None = None
    executed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_task(
    *,
    action: str = "device_publish",
    account_id: Any = None,
    device_id: Any = None,
    scheduled_at: datetime | None = None,
) -> _FakeTask:
    return _FakeTask(
        id=uuid4(),
        plan_id=uuid4(),
        device_id=device_id or uuid4(),
        account_id=account_id or uuid4(),
        action=action,
        request_id=str(uuid4()),
        scheduled_at=scheduled_at or datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc),
    )


def fixed_clock(at: datetime):
    def clock() -> datetime:
        return at
    return clock


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_capacity_initial(self):
        b = TokenBucket(capacity=30, refill_rate=1 / 30)
        assert b.tokens == 30

    @pytest.mark.asyncio
    async def test_acquire_consumes_token(self):
        b = TokenBucket(capacity=2, refill_rate=1 / 30)
        await b.acquire(timeout=1)
        await b.acquire(timeout=1)
        # 桶空，下一次必须等待
        assert b.tokens < 1

    @pytest.mark.asyncio
    async def test_refill_over_time(self):
        # 1 token / 0.01s 速率，capacity=2
        b = TokenBucket(capacity=2, refill_rate=100.0)
        await b.acquire(timeout=1)
        await b.acquire(timeout=1)
        # 等待 0.02s 应该再得一个
        await asyncio.sleep(0.02)
        b._refill()
        assert b.tokens >= 1

    @pytest.mark.asyncio
    async def test_acquire_timeout(self):
        b = TokenBucket(capacity=1, refill_rate=1 / 30)  # 30s/token
        await b.acquire(timeout=0.1)
        with pytest.raises(RateLimitTimeout):
            await b.acquire(timeout=0.1)

    @pytest.mark.asyncio
    async def test_acquire_blocks_until_token(self):
        # 桶空，refill 0.05s/token，验证 acquire 在 ~0.05s 内成功
        b = TokenBucket(capacity=1, refill_rate=20.0)
        await b.acquire(timeout=1)
        start = time.monotonic()
        await b.acquire(timeout=1)
        elapsed = time.monotonic() - start
        assert 0.01 < elapsed < 0.5

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            TokenBucket(capacity=0, refill_rate=1)
        with pytest.raises(ValueError):
            TokenBucket(capacity=1, refill_rate=0)


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_below_threshold_closed(self):
        cb = CircuitBreaker(window=600, threshold=5, cool_off=1800)
        for _ in range(4):
            cb.record_failure()
        assert cb.is_open() is False

    def test_at_threshold_open(self):
        cb = CircuitBreaker(window=600, threshold=5, cool_off=1800)
        for _ in range(5):
            cb.record_failure()
        assert cb.is_open() is True

    def test_window_prunes_old_failures(self, monkeypatch):
        cb = CircuitBreaker(window=10, threshold=5, cool_off=1800)
        now = 1000.0
        monkeypatch.setattr("matrix.scheduler.circuit_breaker.monotonic", lambda: now)
        for _ in range(5):
            cb.record_failure()
        assert cb.is_open()
        assert len(cb.failures) == 5

        # 推进到窗口外后再次 record_failure：旧的应被剪光
        now += 11
        cb.record_failure()
        assert len(cb.failures) == 1  # 只剩新加的这次
        # 但 cool_off 仍生效（open_until 没被剪枝逻辑重置）
        assert cb.is_open() is True

        # cool_off 过去后才真正合上
        now += 1800
        assert cb.is_open() is False

    def test_cool_off_eventually_closes(self, monkeypatch):
        cb = CircuitBreaker(window=600, threshold=2, cool_off=10)
        now = 0.0
        monkeypatch.setattr("matrix.scheduler.circuit_breaker.monotonic", lambda: now)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open()
        now += 11
        assert cb.is_open() is False

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            CircuitBreaker(window=0, threshold=1)
        with pytest.raises(ValueError):
            CircuitBreaker(window=1, threshold=0)
        with pytest.raises(ValueError):
            CircuitBreaker(window=1, threshold=1, cool_off=-1)

    def test_record_failure_returns_true_only_on_transition(self, monkeypatch):
        """closed→open 边界返回 True；打开后继续失败返回 False。"""
        cb = CircuitBreaker(window=600, threshold=2, cool_off=60)
        now = 1000.0
        monkeypatch.setattr("matrix.scheduler.circuit_breaker.monotonic", lambda: now)
        assert cb.record_failure() is False  # 未到阈值
        assert cb.record_failure() is True  # 触发 closed→open
        assert cb.record_failure() is False  # 已打开，不再算边界

    def test_retry_after(self, monkeypatch):
        cb = CircuitBreaker(window=600, threshold=1, cool_off=60)
        now = 1000.0
        monkeypatch.setattr("matrix.scheduler.circuit_breaker.monotonic", lambda: now)
        assert cb.retry_after() == 0.0
        cb.record_failure()
        assert cb.retry_after() == pytest.approx(60.0)
        now += 30
        assert cb.retry_after() == pytest.approx(30.0)
        now += 31
        assert cb.retry_after() == 0.0


# ---------------------------------------------------------------------------
# PerDeviceCircuitBreaker（W3：按设备分桶）
# ---------------------------------------------------------------------------


class TestPerDeviceCircuitBreaker:
    def test_buckets_are_independent(self):
        from matrix.scheduler.circuit_breaker import PerDeviceCircuitBreaker

        cb = PerDeviceCircuitBreaker(window=600, threshold=2, cool_off=60)
        a, b = uuid4(), uuid4()
        cb.record_failure(a)
        cb.record_failure(a)
        assert cb.is_open(a) is True
        assert cb.is_open(b) is False
        # B 的一次失败不影响 A 的计数
        assert cb.record_failure(b) is False
        assert cb.is_open(b) is False

    def test_transition_flag_per_device(self):
        from matrix.scheduler.circuit_breaker import PerDeviceCircuitBreaker

        cb = PerDeviceCircuitBreaker(window=600, threshold=1, cool_off=60)
        a, b = uuid4(), uuid4()
        assert cb.record_failure(a) is True  # A 熔断
        assert cb.record_failure(b) is True  # B 也独立触发自己的边界
        assert cb.record_failure(a) is False

    def test_retry_after_per_device(self, monkeypatch):
        from matrix.scheduler.circuit_breaker import PerDeviceCircuitBreaker

        cb = PerDeviceCircuitBreaker(window=600, threshold=1, cool_off=120)
        now = 1000.0
        monkeypatch.setattr("matrix.scheduler.circuit_breaker.monotonic", lambda: now)
        a = uuid4()
        cb.record_failure(a)
        assert 0 < cb.retry_after(a) <= 120
        assert cb.retry_after(uuid4()) == 0.0

    def test_reset(self):
        from matrix.scheduler.circuit_breaker import PerDeviceCircuitBreaker

        cb = PerDeviceCircuitBreaker(window=600, threshold=1, cool_off=60)
        a, b = uuid4(), uuid4()
        cb.record_failure(a)
        cb.record_failure(b)
        cb.reset(a)
        assert cb.is_open(a) is False
        assert cb.is_open(b) is True
        cb.reset()
        assert cb.is_open(b) is False


# ---------------------------------------------------------------------------
# jitter
# ---------------------------------------------------------------------------


class TestJitter:
    def test_distribution_shape(self):
        # 大量采样：均值应近似 base * exp(sigma^2/2)
        base = 5.0
        sigma = 0.5
        n = 5000
        samples = [jitter_delay(base, sigma) for _ in range(n)]
        expected = base * math.exp(sigma**2 / 2)
        observed_mean = statistics.mean(samples)
        # 5% 容差（中心极限定理 + 有限样本）
        assert abs(observed_mean - expected) / expected < 0.1

    def test_non_negative_default(self):
        # 99% 的样本在 50x 抖动下不应暴负
        samples = [jitter_delay(1.0, 0.5) for _ in range(200)]
        assert all(s >= 0 for s in samples)

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            jitter_delay(-1.0)
        with pytest.raises(ValueError):
            jitter_delay(1.0, sigma=-0.1)


# ---------------------------------------------------------------------------
# active_window
# ---------------------------------------------------------------------------


class TestActiveWindow:
    def test_default_window_inclusive_start(self):
        # 09:00 本地时间（naive 视为设备本地）
        t = datetime(2026, 7, 8, 9, 0)
        assert is_in_active_window(t, None, "Asia/Shanghai") is True

    def test_default_window_exclusive_end(self):
        t = datetime(2026, 7, 8, 23, 0)
        assert is_in_active_window(t, None, "Asia/Shanghai") is False

    def test_before_window(self):
        t = datetime(2026, 7, 8, 8, 59)
        assert is_in_active_window(t, None, "Asia/Shanghai") is False

    def test_within_window(self):
        t = datetime(2026, 7, 8, 12, 30)
        assert is_in_active_window(t, None, "Asia/Shanghai") is True

    def test_persona_override(self):
        t = datetime(2026, 7, 8, 6, 0)
        # 默认不允许，但 persona 改为 05:00-22:00 时允许
        cfg = {"active_window": {"start": 5, "end": 22}}
        assert is_in_active_window(t, cfg) is True

    def test_unknown_tz_falls_back(self):
        # 未知时区不应抛
        t = datetime(2026, 7, 8, 10, 0)
        assert is_in_active_window(t, None, "Mars/Olympus_Mons") is True


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


def _no_jitter_limiter(**overrides) -> RateLimiter:
    """构造一个 jitter=0 的 limiter，避免 sleep 拖慢测试。"""
    defaults: dict[str, Any] = dict(
        bucket_capacity=30,
        bucket_refill_rate=1 / 30,
        jitter_base=0.0,
        jitter_sigma=0.0,
    )
    defaults.update(overrides)
    return RateLimiter(**defaults)


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_throttle_passes_in_window(self):
        rl = _no_jitter_limiter()
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        task = make_task(action="device_publish", scheduled_at=noon)
        d = await rl.throttle(task)
        assert d.ok
        assert d.jitter_seconds == 0.0

    @pytest.mark.asyncio
    async def test_throttle_blocks_outside_window(self):
        rl = _no_jitter_limiter()
        late = datetime(2026, 7, 8, 23, 30)
        rl._clock = fixed_clock(late)  # type: ignore[assignment]
        task = make_task(action="device_publish", scheduled_at=late)
        d = await rl.throttle(task)
        assert not d.ok
        assert d.reason == "out_of_active_window"

    @pytest.mark.asyncio
    async def test_throttle_blocks_when_circuit_open(self):
        rl = _no_jitter_limiter(
            breaker=CircuitBreaker(window=600, threshold=1, cool_off=1800),
        )
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        rl.breaker.record_failure()  # 立即开熔断
        task = make_task(scheduled_at=noon)
        d = await rl.throttle(task)
        assert not d.ok
        assert d.reason == "circuit_open"

    @pytest.mark.asyncio
    async def test_daily_cap_account_publish(self):
        rl = _no_jitter_limiter()
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        account = uuid4()
        device = uuid4()
        for _ in range(3):
            await rl.record(make_task(action="device_publish", account_id=account, device_id=device))
        task = make_task(action="device_publish", account_id=account, device_id=device)
        d = await rl.throttle(task)
        assert not d.ok
        assert d.reason == "daily_cap_account_publish"

    @pytest.mark.asyncio
    async def test_daily_cap_device_publish(self):
        rl = _no_jitter_limiter()
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        device = uuid4()
        # 跨账号：单设备 5 条 / 单账号 3 条 → 设 2 个账号各 3 条 = 6 条 → 触发设备上限
        a1, a2 = uuid4(), uuid4()
        for _ in range(3):
            await rl.record(make_task(action="device_publish", account_id=a1, device_id=device))
        for _ in range(2):
            await rl.record(make_task(action="device_publish", account_id=a2, device_id=device))
        task = make_task(action="device_publish", account_id=a2, device_id=device)
        d = await rl.throttle(task)
        assert not d.ok
        assert d.reason == "daily_cap_device_publish"

    @pytest.mark.asyncio
    async def test_daily_cap_device_interact(self):
        rl = _no_jitter_limiter()
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        device = uuid4()
        for _ in range(30):
            await rl.record(make_task(action="device_like", device_id=device))
        task = make_task(action="device_like", device_id=device)
        d = await rl.throttle(task)
        assert not d.ok
        assert d.reason == "daily_cap_device_interact"

    @pytest.mark.asyncio
    async def test_daily_cap_device_comment(self):
        """v0.6: comment action 也走 device_interact quota（30/day）。"""
        rl = _no_jitter_limiter()
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        device = uuid4()
        for _ in range(30):
            await rl.record(make_task(action="device_comment", device_id=device))
        task = make_task(action="device_comment", device_id=device)
        d = await rl.throttle(task)
        assert not d.ok
        assert d.reason == "daily_cap_device_interact"

    @pytest.mark.asyncio
    async def test_account_cap_device_comment(self):
        """v0.6: account 维度的 comment cap（20/day）。"""
        rl = _no_jitter_limiter()
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        device = uuid4()
        account = uuid4()
        for _ in range(20):
            await rl.record(
                make_task(action="device_comment", device_id=device, account_id=account)
            )
        task = make_task(action="device_comment", device_id=device, account_id=account)
        d = await rl.throttle(task)
        assert not d.ok
        assert d.reason == "daily_cap_account_interact"

    @pytest.mark.asyncio
    async def test_token_bucket_exhaustion(self):
        # capacity=1 → 第二次需等令牌（timeout 短会超时）
        rl = RateLimiter(
            bucket_capacity=1,
            bucket_refill_rate=1 / 30,
            jitter_base=0.0,
            jitter_sigma=0.0,
        )
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        bucket = await rl._get_bucket("acct")
        # 直接用底层桶，模拟桶被外部耗尽
        await bucket.acquire(timeout=0.1)
        # 此时再用 rate_limiter.acquire 包装超时 0.05s
        with pytest.raises(RateLimitTimeout):
            await bucket.acquire(timeout=0.05)

    @pytest.mark.asyncio
    async def test_execute_records_success_and_counts(self):
        async def runner(_: TaskLike) -> bool:
            return True

        rl = _no_jitter_limiter()
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        task = make_task()
        d = await rl.execute(task, runner)
        assert d.ok
        # 再跑 2 次（仍 < 3）
        await rl.execute(task, runner)
        await rl.execute(task, runner)
        # 第 4 次 → 触发日上限
        d = await rl.execute(task, runner)
        assert not d.ok
        assert d.reason == "daily_cap_account_publish"

    @pytest.mark.asyncio
    async def test_execute_records_breaker_failure(self):
        async def runner(_: TaskLike) -> bool:
            return False

        rl = _no_jitter_limiter(
            breaker=CircuitBreaker(window=600, threshold=2, cool_off=1800),
        )
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        for _ in range(2):
            task = make_task()  # 新任务避免日上限
            await rl.execute(task, runner)
        assert rl.breaker.is_open()

    @pytest.mark.asyncio
    async def test_execute_runner_exception(self):
        async def runner(_: TaskLike) -> bool:
            raise RuntimeError("boom")

        rl = _no_jitter_limiter(
            breaker=CircuitBreaker(window=600, threshold=1, cool_off=1800),
        )
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        d = await rl.execute(make_task(), runner)
        assert not d.ok
        assert d.reason == "runner_error"
        assert rl.breaker.is_open()

    @pytest.mark.asyncio
    async def test_jitter_injection_returns_positive(self):
        # 用固定 sigma=0.1 让抖动不会跑太远，但保证非零
        rl = RateLimiter(
            bucket_capacity=30,
            bucket_refill_rate=1 / 30,
            jitter_base=0.1,
            jitter_sigma=0.1,
        )
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        task = make_task()
        d = await rl.throttle(task)
        assert d.ok
        assert d.jitter_seconds >= 0

    @pytest.mark.asyncio
    async def test_throttle_does_not_sleep_jitter_itself(self):
        """W3：throttle 只返回 jitter_seconds，不自己 sleep（否则调用方再睡=双重睡眠）。"""
        rl = RateLimiter(
            bucket_capacity=30,
            bucket_refill_rate=1 / 30,
            jitter_base=50.0,  # 若内部 sleep 会阻塞 ~50s
            jitter_sigma=0.1,
        )
        noon = datetime(2026, 7, 8, 12, 0)
        rl._clock = fixed_clock(noon)  # type: ignore[assignment]
        start = time.monotonic()
        d = await rl.throttle(make_task())
        elapsed = time.monotonic() - start
        assert d.ok
        assert d.jitter_seconds > 1.0
        assert elapsed < 1.0

    def test_default_clock_is_utc_aware(self):
        """W3：默认 clock 必须返回 UTC aware 时间（naive 与 aware 比较会 TypeError）。"""
        rl = RateLimiter(jitter_base=0.0, jitter_sigma=0.0)
        assert rl._clock().tzinfo is not None

    def test_default_bucket_capacity_matches_account_interact_cap(self):
        """W3：令牌桶默认容量与账号日互动上限（20）一致，不再用矛盾的 30。"""
        rl = RateLimiter(jitter_base=0.0, jitter_sigma=0.0)
        assert rl.bucket_capacity == rl.account_interact_per_day == 20


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class _RecorderLoader:
    def __init__(self, batches: list[list[TaskLike]]) -> None:
        self.batches = list(batches)
        self.calls = 0

    async def load_pending(self, now: datetime, limit: int) -> list[TaskLike]:
        self.calls += 1
        if self.batches:
            return self.batches.pop(0)
        return []


class _RecorderWriter(TaskStatusWriter):
    def __init__(self) -> None:
        self.running: list[object] = []
        self.success: list[object] = []
        self.failed: list[tuple[object, dict]] = []
        self.pending: list[tuple[object, datetime]] = []

    async def mark_running(self, task: TaskLike) -> None:
        self.running.append(task.id)

    async def mark_success(self, task: TaskLike, executed_at: datetime) -> None:
        self.success.append(task.id)

    async def mark_failed(self, task: TaskLike, error: dict, executed_at: datetime) -> None:
        self.failed.append((task.id, error))

    async def mark_pending(self, task: TaskLike, scheduled_at: datetime) -> None:
        self.pending.append((task.id, scheduled_at))


class _RecorderExecutor(TaskExecutor):
    def __init__(self, results: list[bool]) -> None:
        self.results = list(results)
        self.calls: list[TaskLike] = []

    async def execute(self, task: TaskLike) -> bool:
        self.calls.append(task)
        if self.results:
            return self.results.pop(0)
        return True


class TestScheduler:
    @pytest.mark.asyncio
    async def test_dispatches_pending_and_marks_success(self):
        tasks = [make_task(action="device_collect_metrics") for _ in range(3)]
        loader = _RecorderLoader([tasks])
        writer = _RecorderWriter()
        executor = _RecorderExecutor([True, True, True])
        rl = _no_jitter_limiter()

        s = Scheduler(
            loader=loader,  # type: ignore[arg-type]
            writer=writer,  # type: ignore[arg-type]
            executor=executor,  # type: ignore[arg-type]
            rate_limiter=rl,
            poll_interval=0.01,
        )

        # 在第一帧 dispatch 完成后再停
        async def trigger_stop():
            for _ in range(50):
                if len(writer.success) == 3:
                    s.stop()
                    return
                await asyncio.sleep(0.005)

        await asyncio.gather(s.run(), trigger_stop())

        assert writer.running == [t.id for t in tasks]
        assert writer.success == [t.id for t in tasks]
        assert writer.failed == []
        assert len(executor.calls) == 3

    @pytest.mark.asyncio
    async def test_marks_failed_when_executor_false(self):
        tasks = [make_task(action="device_collect_metrics"), make_task(action="device_collect_metrics")]
        loader = _RecorderLoader([tasks])
        writer = _RecorderWriter()
        executor = _RecorderExecutor([True, False])
        rl = _no_jitter_limiter()

        s = Scheduler(
            loader=loader,  # type: ignore[arg-type]
            writer=writer,  # type: ignore[arg-type]
            executor=executor,  # type: ignore[arg-type]
            rate_limiter=rl,
            poll_interval=0.01,
        )

        async def trigger_stop():
            for _ in range(50):
                if len(writer.success) + len(writer.failed) == 2:
                    s.stop()
                    return
                await asyncio.sleep(0.005)

        await asyncio.gather(s.run(), trigger_stop())

        assert len(writer.success) == 1
        assert len(writer.failed) == 1
        assert writer.failed[0][1]["code"] == "EXECUTOR_FALSE"

    @pytest.mark.asyncio
    async def test_marks_failed_when_executor_raises(self):
        class RaisingExecutor(TaskExecutor):
            async def execute(self, task: TaskLike) -> bool:
                raise RuntimeError("oops")

        tasks = [make_task(action="device_collect_metrics")]
        loader = _RecorderLoader([tasks])
        writer = _RecorderWriter()
        rl = _no_jitter_limiter()

        s = Scheduler(
            loader=loader,  # type: ignore[arg-type]
            writer=writer,  # type: ignore[arg-type]
            executor=RaisingExecutor(),  # type: ignore[arg-type]
            rate_limiter=rl,
            poll_interval=0.01,
        )

        async def trigger_stop():
            for _ in range(50):
                if writer.failed:
                    s.stop()
                    return
                await asyncio.sleep(0.005)

        await asyncio.gather(s.run(), trigger_stop())

        assert len(writer.failed) == 1
        assert writer.failed[0][1]["code"] == "EXECUTOR_RAISED"

    @pytest.mark.asyncio
    async def test_loader_exception_does_not_crash(self):
        class BoomLoader(TaskLoader):
            async def load_pending(self, now: datetime, limit: int) -> list[TaskLike]:
                raise RuntimeError("db down")

        writer = _RecorderWriter()
        executor = _RecorderExecutor([])
        rl = _no_jitter_limiter()

        s = Scheduler(
            loader=BoomLoader(),  # type: ignore[arg-type]
            writer=writer,  # type: ignore[arg-type]
            executor=executor,  # type: ignore[arg-type]
            rate_limiter=rl,
            poll_interval=0.01,
        )
        s._stop_event.set()
        await s.run()
        # 跑过至少一帧，writer 没收到任何东西
        assert writer.running == []
        assert writer.success == []

    @pytest.mark.asyncio
    async def test_circuit_open_reschedules_to_pending(self):
        """W3：执行器抛 CircuitOpen → 任务回 pending 并推迟 scheduled_at，不 mark_failed。"""
        from matrix.scheduler.circuit_breaker import CircuitOpen

        class OpenExecutor(TaskExecutor):
            async def execute(self, task: TaskLike) -> bool:
                raise CircuitOpen(retry_after=120.0)

        now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
        tasks = [make_task(action="device_collect_metrics")]
        loader = _RecorderLoader([tasks])
        writer = _RecorderWriter()
        rl = _no_jitter_limiter()

        s = Scheduler(
            loader=loader,  # type: ignore[arg-type]
            writer=writer,  # type: ignore[arg-type]
            executor=OpenExecutor(),  # type: ignore[arg-type]
            rate_limiter=rl,
            poll_interval=0.01,
            clock=fixed_clock(now),
        )

        async def trigger_stop():
            for _ in range(50):
                if writer.pending:
                    s.stop()
                    return
                await asyncio.sleep(0.005)

        await asyncio.gather(s.run(), trigger_stop())

        assert writer.failed == []
        assert writer.success == []
        assert len(writer.pending) == 1
        task_id, scheduled_at = writer.pending[0]
        assert task_id == tasks[0].id
        # 推迟到冷却结束（120s）之后
        assert (scheduled_at - now).total_seconds() == pytest.approx(120.0)

    @pytest.mark.asyncio
    async def test_sweep_reclaims_stale_running(self):
        """W3：主循环定期调用 loader.reclaim_stale_running 回收卡死任务。"""
        calls: list[dict] = []

        class SweepLoader(_RecorderLoader):
            async def reclaim_stale_running(
                self, now, *, stale_after_seconds, max_attempts
            ) -> int:
                calls.append(
                    {
                        "stale_after_seconds": stale_after_seconds,
                        "max_attempts": max_attempts,
                    }
                )
                return 0

        loader = SweepLoader([])
        writer = _RecorderWriter()
        executor = _RecorderExecutor([])
        rl = _no_jitter_limiter()

        s = Scheduler(
            loader=loader,  # type: ignore[arg-type]
            writer=writer,  # type: ignore[arg-type]
            executor=executor,  # type: ignore[arg-type]
            rate_limiter=rl,
            poll_interval=0.01,
            sweep_interval=0.02,
            stale_running_seconds=1800.0,
            max_attempts=5,
        )

        async def trigger_stop():
            await asyncio.sleep(0.06)
            s.stop()

        await asyncio.gather(s.run(), trigger_stop())

        # 0.06s / 0.02s 间隔 → 至少扫 2 次
        assert len(calls) >= 2
        assert calls[0]["stale_after_seconds"] == 1800.0
        assert calls[0]["max_attempts"] == 5

    @pytest.mark.asyncio
    async def test_sweep_skipped_when_loader_has_no_reclaim(self):
        """loader 没有 reclaim_stale_running（如测试假 loader）时主循环不受影响。"""
        loader = _RecorderLoader([])
        writer = _RecorderWriter()
        executor = _RecorderExecutor([])
        rl = _no_jitter_limiter()

        s = Scheduler(
            loader=loader,  # type: ignore[arg-type]
            writer=writer,  # type: ignore[arg-type]
            executor=executor,  # type: ignore[arg-type]
            rate_limiter=rl,
            poll_interval=0.01,
            sweep_interval=0.01,
        )

        async def trigger_stop():
            await asyncio.sleep(0.03)
            s.stop()

        await asyncio.gather(s.run(), trigger_stop())
        assert loader.calls >= 1

    @pytest.mark.asyncio
    async def test_stop_event_halts(self):
        loader = _RecorderLoader([])
        writer = _RecorderWriter()
        executor = _RecorderExecutor([])
        rl = _no_jitter_limiter()

        s = Scheduler(
            loader=loader,  # type: ignore[arg-type]
            writer=writer,  # type: ignore[arg-type]
            executor=executor,  # type: ignore[arg-type]
            rate_limiter=rl,
            poll_interval=0.01,
        )

        async def trigger_stop():
            await asyncio.sleep(0.05)
            s.stop()

        await asyncio.gather(s.run(), trigger_stop())
        # 至少跑了 1 帧
        assert loader.calls >= 1
