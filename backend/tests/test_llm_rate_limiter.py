"""LLM 限速器测试（P1-1 回归）。

不连 DB：纯 asyncio + 一个假 LLM（sleep 0.5s 模拟慢响应）。
验证：
  - 全局 Semaphore 真的限制在飞 LLM 请求数
  - 不同 model 各自有独立令牌桶
  - acquire / release 配对正确（不漏 semaphore）
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from matrix.agent.llm_rate_limiter import LLMRateLimiter
from matrix.agent._services import AgentServices, llm_complete, set_services, reset_services
from matrix.llm.clients import CompletionResult
from matrix.scheduler.token_bucket import RateLimitTimeout


class _SleepLLM:
    """每次 complete sleep 0.3s，统计峰值并发。"""

    provider = "fake"

    def __init__(self, sleep: float = 0.3) -> None:
        self.sleep = sleep
        self.in_flight = 0
        self.peak = 0
        self.calls = 0

    async def complete(self, prompt, **_kw):
        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.sleep)
        finally:
            self.in_flight -= 1
        return CompletionResult(
            text="{}",
            model="fake",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=int(self.sleep * 1000),
            provider=self.provider,
            stop_reason="end_turn",
        )


def _make_services(llm, *, semaphore_size: int, capacity: int = 100, refill: float = 100.0):
    # token bucket 容量+补充调大，免得它先卡住测试
    return AgentServices(
        llm=llm,
        kb_retriever=MagicMock(),
        kb_writer=MagicMock(),
        device_publisher=MagicMock(),
        device_collector=MagicMock(),
        notifier=MagicMock(),
        model="fake",
        llm_rate_limiter=LLMRateLimiter(
            semaphore=asyncio.Semaphore(semaphore_size),
            per_model_capacity=capacity,
            per_model_refill_rate=refill,
            timeout=10.0,
        ),
    )


class TestGlobalConcurrencyCap:
    async def test_semaphore_limits_inflight_count(self):
        fake = _SleepLLM(sleep=0.3)
        services = _make_services(fake, semaphore_size=2)
        set_services(services)
        try:
            await asyncio.gather(*[llm_complete(None, "hi") for _ in range(10)])
        finally:
            reset_services()

        assert fake.peak <= 2, f"预期峰值 ≤ 2，实际 {fake.peak}"
        assert fake.calls == 10


class TestPerModelBucket:
    async def test_two_models_independent_buckets(self):
        """两个 model 应该各自有令牌桶：一个被偷光不会影响另一个。"""

        rl = LLMRateLimiter(
            semaphore=asyncio.Semaphore(100),
            per_model_capacity=2,
            per_model_refill_rate=0.01,  # 极慢补充；让"瞬时"桶很快空
            timeout=2.0,
        )

        # 桶还没创建时 snapshot 该 model 是 0
        assert rl.snapshot("sonnet") == {"sonnet": 0.0}

        # 抢 2 次 sonnet → 满
        await rl.acquire("sonnet")
        await rl.acquire("sonnet")
        # 第三次抢 sonnet 应该超时（桶空了 + refill 极慢 + timeout 2s）
        with pytest.raises(RateLimitTimeout):
            await rl.acquire("sonnet")
        # 但 haiku 的桶是全新的，立刻抢到
        await rl.acquire("haiku")
        rl.release("haiku")  # 别 leak semaphore

        # 释放两次 sonnet slot（acquire 抢的是 semaphore + bucket）
        rl.release("sonnet")
        rl.release("sonnet")


class TestReleaseSemantics:
    async def test_release_does_not_return_tokens(self):
        """``release`` 只放 semaphore，不把令牌"还回"桶（桶消费即扣）。"""
        rl = LLMRateLimiter(
            semaphore=asyncio.Semaphore(10),
            per_model_capacity=3,
            per_model_refill_rate=0.001,  # 不补
            timeout=0.5,
        )
        # 抢 3 次（桶满）
        await rl.acquire("m")
        await rl.acquire("m")
        await rl.acquire("m")
        # 这时桶空，下一次 acquire 应超时
        with pytest.raises(RateLimitTimeout):
            await rl.acquire("m")
        # release 不补桶：还是空
        rl.release("m")
        rl.release("m")
        rl.release("m")
        with pytest.raises(RateLimitTimeout):
            await rl.acquire("m")
