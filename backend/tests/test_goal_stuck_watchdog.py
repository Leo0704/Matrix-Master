"""GoalStuckWatchdog 测试（P2-2 回归）。

镜像 ``test_watcher.py`` 的结构（FakeScanner + Config defaults + scan_once
+ start/stop + respawn-after-death）。
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from matrix.agent.goal_stuck_watchdog import (
    GoalStuckScanner,
    GoalStuckWatchdog,
    GoalStuckWatchdogConfig,
)


# ---------------------------------------------------------------------------
# Stub
# ---------------------------------------------------------------------------


class FakeScanner:
    """可注入的 fake scanner：list of stuck goal_ids + advance_one 桩。"""

    def __init__(
        self,
        stuck: list | None = None,
        *,
        raise_on_find: Exception | None = None,
        raise_on_advance: Exception | None = None,
    ) -> None:
        self.stuck = list(stuck or [])
        self.advance_calls: list = []
        self.raise_on_find = raise_on_find
        self.raise_on_advance = raise_on_advance

    async def find_stuck_pending(self, now, threshold_sec):
        if self.raise_on_find is not None:
            raise self.raise_on_find
        return list(self.stuck)

    async def advance_one(self, goal_id):
        self.advance_calls.append(goal_id)
        if self.raise_on_advance is not None:
            raise self.raise_on_advance
        return True


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestGoalStuckWatchdogConfig:
    def test_defaults(self):
        cfg = GoalStuckWatchdogConfig()
        assert cfg.poll_interval_sec == 60.0
        assert cfg.stuck_threshold_sec == 120
        assert cfg.dry_run is False
        assert cfg.max_per_tick == 20


# ---------------------------------------------------------------------------
# _scan_once
# ---------------------------------------------------------------------------


class TestScanOnce:
    async def test_no_stuck_returns_zero(self):
        wd = GoalStuckWatchdog(FakeScanner(stuck=[]), config=GoalStuckWatchdogConfig())
        n = await wd._scan_once()
        assert n == 0

    async def test_dry_run_does_not_call_advance(self):
        scanner = FakeScanner(stuck=["g1", "g2"])
        cfg = GoalStuckWatchdogConfig(dry_run=True)
        wd = GoalStuckWatchdog(scanner, config=cfg)
        n = await wd._scan_once()
        assert n == 0
        assert scanner.advance_calls == []

    async def test_active_mode_calls_advance_and_notifier(self):
        scanner = FakeScanner(stuck=["g1", "g2"])
        notifier_calls: list = []

        async def notifier(name, payload):
            notifier_calls.append((name, payload))

        cfg = GoalStuckWatchdogConfig(dry_run=False)
        wd = GoalStuckWatchdog(scanner, config=cfg, notifier=notifier)
        n = await wd._scan_once()
        assert n == 2
        assert set(scanner.advance_calls) == {"g1", "g2"}
        assert all(name == "goal_stuck_watchdog_rescued" for name, _ in notifier_calls)
        assert any("g1" in str(p) for _, p in notifier_calls)

    async def test_advance_failure_does_not_stop_iteration(self):
        scanner = FakeScanner(stuck=["g1", "g2", "g3"])
        notifier_calls: list = []

        async def notifier(name, payload):
            notifier_calls.append((name, payload))

        cfg = GoalStuckWatchdogConfig(dry_run=False)

        async def advance_advancing(*a, **kw):
            gid = a[1] if len(a) > 1 else a[0]
            scanner.advance_calls.append(gid)
            if gid == "g2":
                raise RuntimeError("fake failure")
            return True

        scanner.advance_one = advance_advancing
        wd = GoalStuckWatchdog(scanner, config=cfg, notifier=notifier)
        n = await wd._scan_once()
        # advance_one 抛了但被扫描整 try 住？不行：scanner 内部 try/except 已吃掉
        # 所以应继续推进未抛的 g1/g3，g2 失败但整 tick 不死
        assert n == 2  # g1 + g3 推进成功
        assert set(scanner.advance_calls) == {"g1", "g2", "g3"}

    async def test_scanner_exception_returns_zero(self):
        scanner = FakeScanner(raise_on_find=RuntimeError("DB down"))
        wd = GoalStuckWatchdog(scanner, config=GoalStuckWatchdogConfig())
        n = await wd._scan_once()
        assert n == 0


# ---------------------------------------------------------------------------
# start / stop 生命周期（含 respawn-after-death 回归）
# ---------------------------------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_task_and_stop_is_graceful(self):
        scanner = FakeScanner(stuck=[])
        wd = GoalStuckWatchdog(
            scanner,
            config=GoalStuckWatchdogConfig(poll_interval_sec=0.05),
        )
        task = wd.start()
        assert task is not None
        await asyncio.sleep(0.15)  # 至少 2 个 tick
        await wd.stop()
        assert task.done() or task.cancelling() is not None

    @pytest.mark.asyncio
    async def test_respawn_after_death(self):
        """P2-2 核心回归：task done 后再 start() 必须能拉起新 task。"""
        scanner = FakeScanner(stuck=[])
        wd = GoalStuckWatchdog(
            scanner,
            config=GoalStuckWatchdogConfig(poll_interval_sec=0.05),
        )

        # 第一次 start
        first_task = wd.start()
        assert first_task is not None
        assert not first_task.done()

        # 模拟 silent death（手动 cancel + 等待）
        first_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first_task
        assert first_task.done()

        # 再 start 必须成功（之前版本的 bug：is_running=False 后没人能再起）
        second_task = wd.start()
        assert second_task is not None
        assert not second_task.done()
        # 而且是新 task
        assert second_task is not first_task

        await wd.stop()
        assert second_task.done() or second_task.cancelling() is not None
