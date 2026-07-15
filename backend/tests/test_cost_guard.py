"""matrix.agent.cost_guard 单元测试 + llm_complete 集成测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from matrix.agent.cost_guard import CostGuard, LLMCostLimitExceeded


class _FakeCounter:
    """用 dict 模拟 :class:`DbDailyCounter` 的 get/add 行为。"""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str, str, date], int] = {}

    async def get(self, scope, key, kind, day):
        return self.store.get((scope, str(key), kind, day), 0)

    async def add(self, scope, key, kind, day, amount: int = 1):
        k = (scope, str(key), kind, day)
        self.store[k] = self.store.get(k, 0) + amount
        return self.store[k]


class TestCostGuardBasics:
    @pytest.mark.asyncio
    async def test_records_both_goal_and_global(self):
        c = _FakeCounter()
        g = CostGuard(counter=c, per_goal_limit=1000, global_limit=5000)
        await g.record(goal_id="g1", prompt_tokens=100, completion_tokens=50)
        assert await c.get("llm_cost", "g1", "tokens", g._today()) == 150
        assert await c.get("llm_cost", "GLOBAL", "tokens", g._today()) == 150

    @pytest.mark.asyncio
    async def test_per_goal_cap_raises(self):
        c = _FakeCounter()
        g = CostGuard(counter=c, per_goal_limit=100, global_limit=10000)
        await g.record(goal_id="g1", prompt_tokens=50, completion_tokens=40)
        # 第二次累到 90+60=150 → 超
        with pytest.raises(LLMCostLimitExceeded) as exc:
            await g.record(goal_id="g1", prompt_tokens=50, completion_tokens=10)
        assert exc.value.scope == "goal"
        assert exc.value.used == 150
        assert exc.value.limit == 100
        assert exc.value.key == "g1"

    @pytest.mark.asyncio
    async def test_global_cap_raises_even_when_per_goal_fine(self):
        c = _FakeCounter()
        g = CostGuard(counter=c, per_goal_limit=1000, global_limit=100)
        # goal1: 50+30=80, 累 80 OK
        await g.record(goal_id="g1", prompt_tokens=50, completion_tokens=30)
        # goal2: 50+10=60, 累 80+60=140 > 100 → global 超
        with pytest.raises(LLMCostLimitExceeded) as exc:
            await g.record(goal_id="g2", prompt_tokens=50, completion_tokens=10)
        assert exc.value.scope == "global"
        assert exc.value.used == 140

    @pytest.mark.asyncio
    async def test_no_goal_id_skips_per_goal_check(self):
        c = _FakeCounter()
        g = CostGuard(counter=c, per_goal_limit=10, global_limit=1000)
        # 50 < 10 不能超 per-goal（压根没记 per-goal）
        await g.record(goal_id=None, prompt_tokens=30, completion_tokens=20)
        assert await c.get("llm_cost", "GLOBAL", "tokens", g._today()) == 50

    @pytest.mark.asyncio
    async def test_zero_tokens_noop(self):
        c = _FakeCounter()
        g = CostGuard(counter=c)
        await g.record(goal_id="g1", prompt_tokens=0, completion_tokens=0)
        assert c.store == {}

    @pytest.mark.asyncio
    async def test_disabled_guard_does_nothing(self):
        c = _FakeCounter()
        g = CostGuard(counter=c, enabled=False, per_goal_limit=10)
        # 关闭时即使超限也不抛
        await g.record(goal_id="g1", prompt_tokens=1000, completion_tokens=1000)
        assert c.store == {}

    @pytest.mark.asyncio
    async def test_counter_failure_does_not_break_flow(self):
        broken = AsyncMock()
        broken.add = AsyncMock(side_effect=RuntimeError("db down"))
        g = CostGuard(counter=broken)
        # 计数器挂掉不能挡主流程
        await g.record(goal_id="g1", prompt_tokens=10, completion_tokens=5)

    def test_estimate_tokens_basic(self):
        assert CostGuard.estimate_tokens("") == 0
        assert CostGuard.estimate_tokens("hi") == 1  # max(1, 0)
        assert CostGuard.estimate_tokens("a" * 100) == 25


class TestLlmCompleteIntegration:
    """llm_complete 是否真的把 token 喂给 cost_guard。"""

    @pytest.mark.asyncio
    async def test_records_tokens_from_completion_result(self):
        from matrix.agent import _services as services_mod
        from matrix.agent._services import AgentServices, llm_complete, set_services

        recorded: list[dict] = []

        class FakeGuard:
            async def record(self, *, goal_id, prompt_tokens, completion_tokens):
                recorded.append(
                    {"goal_id": goal_id, "pt": prompt_tokens, "ct": completion_tokens}
                )

        class FakeLLM:
            model = "fake"

            async def complete(self, prompt, **kw):
                from matrix.llm.clients import CompletionResult

                return CompletionResult(
                    text="ok",
                    model="fake",
                    prompt_tokens=10,
                    completion_tokens=20,
                    latency_ms=5,
                    provider="fake",
                )

        set_services(
            AgentServices(
                llm=FakeLLM(),
                kb_retriever=MagicMock(),
                kb_writer=MagicMock(),
                device_publisher=MagicMock(),
                device_collector=MagicMock(),
                notifier=MagicMock(),
                cost_guard=FakeGuard(),
            )
        )
        try:
            result = await llm_complete("sys", "user", goal_id="g-x")
            assert result == "ok"
            assert len(recorded) == 1
            assert recorded[0]["pt"] == 10
            assert recorded[0]["ct"] == 20
            assert recorded[0]["goal_id"] == "g-x"
        finally:
            services_mod.reset_services()

    @pytest.mark.asyncio
    async def test_falls_back_to_estimate_when_usage_zero(self):
        from matrix.agent import _services as services_mod
        from matrix.agent._services import AgentServices, llm_complete, set_services

        recorded: list[dict] = []

        class FakeGuard:
            async def record(self, *, goal_id, prompt_tokens, completion_tokens):
                recorded.append(
                    {"goal_id": goal_id, "pt": prompt_tokens, "ct": completion_tokens}
                )

        class FakeLLM:
            async def complete(self, prompt, **kw):
                from matrix.llm.clients import CompletionResult

                # usage 全 0 → 触发 fallback 估
                return CompletionResult(
                    text="ok",
                    model="fake",
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=5,
                    provider="fake",
                )

        from unittest.mock import MagicMock

        set_services(
            AgentServices(
                llm=FakeLLM(),
                kb_retriever=MagicMock(),
                kb_writer=MagicMock(),
                device_publisher=MagicMock(),
                device_collector=MagicMock(),
                notifier=MagicMock(),
                cost_guard=FakeGuard(),
            )
        )
        try:
            await llm_complete("sys-" * 100, "user-" * 200, goal_id="g-y")
            # 估算：sys-"*100 = 400 字符→100 token；user-"*200 = 1000 字符→250 token
            # completion=svc.max_tokens 默认 1024
            assert len(recorded) == 1
            assert recorded[0]["pt"] == 350
            assert recorded[0]["ct"] == 1024
        finally:
            services_mod.reset_services()

    @pytest.mark.asyncio
    async def test_cost_limit_propagates_through_llm_complete(self):
        from matrix.agent import _services as services_mod
        from matrix.agent._services import AgentServices, llm_complete, set_services

        class TightGuard:
            def __init__(self):
                self.used = 0

            async def record(self, *, goal_id, prompt_tokens, completion_tokens):
                self.used += prompt_tokens + completion_tokens
                if self.used > 25:  # 限 25 tokens；第一次 20 不超
                    raise LLMCostLimitExceeded("goal", self.used, 25, goal_id)

        class FakeLLM:
            async def complete(self, prompt, **kw):
                from matrix.llm.clients import CompletionResult

                return CompletionResult(
                    text="ok",
                    model="fake",
                    prompt_tokens=10,
                    completion_tokens=10,
                    latency_ms=5,
                    provider="fake",
                )

        from unittest.mock import MagicMock

        set_services(
            AgentServices(
                llm=FakeLLM(),
                kb_retriever=MagicMock(),
                kb_writer=MagicMock(),
                device_publisher=MagicMock(),
                device_collector=MagicMock(),
                notifier=MagicMock(),
                cost_guard=TightGuard(),
            )
        )
        try:
            # 第一次：20 tokens，累计 20 不超
            await llm_complete("sys", "user", goal_id="g-z")
            # 第二次：再加 20 → 累计 40 > 25 → 抛
            with pytest.raises(LLMCostLimitExceeded):
                await llm_complete("sys", "user", goal_id="g-z")
        finally:
            services_mod.reset_services()
