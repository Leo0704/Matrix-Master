"""GoalOrchestratorWorker + AgentRunWorker 生命周期测试（P2-2 回归）。

不连 DB（session_factory=None），把 _scan_once / _run_loop_body 整个 patch 掉。
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import patch

import pytest

from matrix.agent.orchestrator_runner import GoalOrchestratorWorker
from matrix.agent.runner import AgentRunWorker


def _make_orchestrator(**kw):
    kw.setdefault("poll_interval", 0.05)
    return GoalOrchestratorWorker(session_factory=None, **kw)


def _make_run_worker(**kw):
    kw.setdefault("poll_interval", 0.05)
    return AgentRunWorker(session_factory=None, **kw)


class TestOrchestratorSelfHeal:
    @pytest.mark.asyncio
    async def test_loop_survives_scan_once_exception(self):
        """_scan_once 抛 Exception 时，worker 不死，能继续下一轮。"""
        w = _make_orchestrator()
        call_count = {"n": 0}

        async def flaky_scan():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated DB glitch")
            return []

        with patch.object(w, "_scan_once", side_effect=flaky_scan):
            w.start()
            await asyncio.sleep(0.20)  # ≥ 3 ticks
            await w.stop()
            assert call_count["n"] >= 2, (
                f"第二次 scan 必须被调到，证实外层 session 自愈；实际 n={call_count['n']}"
            )

    @pytest.mark.asyncio
    async def test_start_respawns_after_task_died(self):
        """P2-2 回归：silent death 后 start() 必须能拉起新 task。"""
        w = _make_orchestrator()
        first = w.start()
        assert first is not None
        # 人为 cancel 模拟 silent death
        first.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first
        assert first.done()

        second = w.start()
        assert second is not None
        assert not second.done()
        assert second is not first

        await w.stop()


class TestAgentRunWorkerSelfHeal:
    @pytest.mark.asyncio
    async def test_loop_survives_scan_once_exception(self):
        w = _make_run_worker()
        call_count = {"n": 0}

        async def flaky_scan():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated")
            return []

        with patch.object(w, "_scan_once", side_effect=flaky_scan):
            w.start()
            await asyncio.sleep(0.20)
            await w.stop()
            assert call_count["n"] >= 2

    @pytest.mark.asyncio
    async def test_start_respawns_after_task_died(self):
        w = _make_run_worker()
        first = w.start()
        assert first is not None
        first.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first
        second = w.start()
        assert second is not None
        assert second is not first
        await w.stop()
