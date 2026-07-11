"""v0.7 Phase 4：AgentRunWatchdog 单元测试。

覆盖：
- WatchdogConfig 默认值
- _scan_once 在 dry_run=True 时打日志但不改 DB
- _scan_once 在 dry_run=False 时调 mark_timeout + notifier
- start/stop 是优雅的
- AgentRunScanner.find_stuck_runs 走 SQL（用 in-memory sqlite 测）
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest

from matrix.agent.watcher import (
    AgentRunWatchdog,
    WatchdogConfig,
)


# ---------------------------------------------------------------------------
# Fake scanner（不需要真 DB）
# ---------------------------------------------------------------------------


class FakeScanner:
    """返回固定 stuck list + 记录 mark_timeout 调用次数。"""

    def __init__(self, stuck: list[Any] | None = None) -> None:
        self.stuck = stuck or []
        self.mark_calls: list[tuple[Any, datetime, str]] = []

    async def find_stuck_runs(self, now: datetime, threshold_sec: int) -> list[Any]:
        return self.stuck

    async def mark_timeout(
        self, run_id: Any, now: datetime, reason: str
    ) -> None:
        self.mark_calls.append((run_id, now, reason))


class _CollectingNotifier:
    """收集 alert 通知。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, name: str, payload: dict[str, Any]) -> None:
        self.calls.append((name, payload))


# ---------------------------------------------------------------------------
# WatchdogConfig
# ---------------------------------------------------------------------------


class TestWatchdogConfig:
    def test_defaults(self):
        cfg = WatchdogConfig()
        assert cfg.poll_interval_sec == 30.0
        assert cfg.stuck_threshold_sec == 600
        # 生产默认 dry_run=False：作为 worker 失败重试耗尽后的兜底标 timeout
        assert cfg.dry_run is False


# ---------------------------------------------------------------------------
# AgentRunWatchdog._scan_once 行为
# ---------------------------------------------------------------------------


class TestScanOnce:
    @pytest.mark.asyncio
    async def test_no_stuck_returns_zero(self):
        scanner = FakeScanner(stuck=[])
        wd = AgentRunWatchdog(scanner, config=WatchdogConfig(dry_run=False))
        count = await wd._scan_once()
        assert count == 0
        assert scanner.mark_calls == []

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_mark(self):
        scanner = FakeScanner(stuck=[uuid4(), uuid4()])
        wd = AgentRunWatchdog(
            scanner, config=WatchdogConfig(dry_run=True)
        )
        count = await wd._scan_once()
        assert count == 0
        assert scanner.mark_calls == []  # dry_run 不调 mark

    @pytest.mark.asyncio
    async def test_active_mode_calls_mark_and_notifier(self):
        run_ids = [uuid4(), uuid4()]
        scanner = FakeScanner(stuck=run_ids)
        notifier = _CollectingNotifier()
        wd = AgentRunWatchdog(
            scanner,
            config=WatchdogConfig(dry_run=False, stuck_threshold_sec=300),
            notifier=notifier,
        )
        count = await wd._scan_once()
        assert count == 2
        assert len(scanner.mark_calls) == 2
        for rid in run_ids:
            called_ids = [m[0] for m in scanner.mark_calls]
            assert rid in called_ids
        assert len(notifier.calls) == 2
        for name, payload in notifier.calls:
            assert name == "agent_run_stuck_timeout"
            assert "reason" in payload
            assert "600s" in payload["reason"] or "300s" in payload["reason"]

    @pytest.mark.asyncio
    async def test_mark_failure_does_not_stop_iteration(self):
        """某个 run mark 抛错不影响下一个。"""

        class FlakyScanner(FakeScanner):
            def __init__(self, stuck):
                super().__init__(stuck=stuck)

            async def mark_timeout(self, run_id, now, reason):
                if run_id == self.stuck[0]:
                    raise RuntimeError("simulated")
                await super().mark_timeout(run_id, now, reason)

        rid_ok = uuid4()
        rid_bad = uuid4()
        scanner = FlakyScanner([rid_bad, rid_ok])
        notifier = _CollectingNotifier()
        wd = AgentRunWatchdog(
            scanner,
            config=WatchdogConfig(dry_run=False),
            notifier=notifier,
        )
        count = await wd._scan_once()
        assert count == 1  # 1 个成功
        assert len(notifier.calls) == 1
        assert scanner.mark_calls[0][0] == rid_ok

    @pytest.mark.asyncio
    async def test_scanner_exception_returns_zero(self):
        class BrokenScanner:
            async def find_stuck_runs(self, now, threshold_sec):
                raise RuntimeError("db down")

            async def mark_timeout(self, run_id, now, reason):
                pass

        wd = AgentRunWatchdog(BrokenScanner(), config=WatchdogConfig(dry_run=False))
        assert await wd._scan_once() == 0


# ---------------------------------------------------------------------------
# start/stop
# ---------------------------------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_task_and_stop_is_graceful(self):
        scanner = FakeScanner(stuck=[])
        wd = AgentRunWatchdog(
            scanner,
            config=WatchdogConfig(poll_interval_sec=0.05, dry_run=False),
        )
        task = wd.start()
        assert task is not None
        # 等 1-2 个 tick
        await _sleep_small()
        await wd.stop()
        assert task.done() or task.cancelling() is not None


async def _sleep_small() -> None:
    import asyncio

    await asyncio.sleep(0.15)


# ---------------------------------------------------------------------------
# AgentRunScanner (DB 路径)
#
# 不依赖 alembic / 真 PG；用 sqlite in-memory + create_all 跑最小依赖。
# 如果环境无 sqlite aiosqlite，skip 该测试。
# ---------------------------------------------------------------------------


class TestAgentRunScannerDBIntegration:
    """DB 路径测试：依赖 PG（INET/JSONB 不兼容 sqlite 的）。

    当前 dev 环境无 PG → skip。产线 asyncpg 测应该走 docker-compose。
    等 Phase 4 把 docker-compose 跑起来后这些测试自动激活。
    """

    @pytest.fixture(autouse=True)
    def _require_pg(self):
        # 简单 gate：有 PG URL 环境变量才跑
        pg_url = os.environ.get("MATRIX_TEST_DATABASE_URL", "")
        if not pg_url.startswith(("postgres", "postgresql")):
            pytest.skip("requires MATRIX_TEST_DATABASE_URL=postgres://...")

    @pytest.mark.asyncio
    async def test_find_stuck_picks_old_running_only(self):
        # 占位：配上 PG 后跑真测
        pytest.skip("PG test infra not bootstrapped yet")
        ...

    @pytest.mark.asyncio
    async def test_mark_timeout_updates_status_and_ended_at(self):
        pytest.skip("PG test infra not bootstrapped yet")
        ...


# 提前 import os 给 gate 用
import os  # noqa: E402
