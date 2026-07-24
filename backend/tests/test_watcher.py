"""v0.7 Phase 4：AgentRunWatchdog 单元测试。

覆盖：
- WatchdogConfig 默认值
- _scan_once 在 dry_run=True 时打日志但不改 DB
- _scan_once 在 dry_run=False 时调 mark_timeout + notifier
- start/stop 是优雅的
- AgentRunScanner.find_stuck_runs 走 SQL（用 in-memory sqlite 测）
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from matrix.agent.watcher import (
    AgentRunWatchdog,
    WatchdogConfig,
    _is_schedule_exempt,
)


# ---------------------------------------------------------------------------
# 排期豁免：sleep 等 scheduled_at 的 run 不算卡死（修 watchdog 误杀）
# ---------------------------------------------------------------------------


class TestScheduleExempt:
    def test_future_scheduled_at_exempts(self):
        now = datetime.now(timezone.utc)
        payload = {
            "preassigned_slot": {
                "scheduled_at": (now + timedelta(hours=3)).isoformat()
            }
        }
        assert _is_schedule_exempt(payload, now) is True

    def test_past_scheduled_at_beyond_grace_not_exempt(self):
        now = datetime.now(timezone.utc)
        payload = {
            "preassigned_slot": {
                "scheduled_at": (now - timedelta(hours=1)).isoformat()
            }
        }
        assert _is_schedule_exempt(payload, now) is False

    def test_past_scheduled_at_within_grace_still_exempt(self):
        now = datetime.now(timezone.utc)
        payload = {
            "preassigned_slot": {
                "scheduled_at": (now - timedelta(minutes=5)).isoformat()
            }
        }
        assert _is_schedule_exempt(payload, now) is True

    def test_z_suffix_parsed(self):
        now = datetime.now(timezone.utc)
        payload = {
            "preassigned_slot": {
                "scheduled_at": (now + timedelta(hours=2))
                .isoformat()
                .replace("+00:00", "Z")
            }
        }
        assert _is_schedule_exempt(payload, now) is True

    def test_missing_or_bad_payload_not_exempt(self):
        now = datetime.now(timezone.utc)
        assert _is_schedule_exempt(None, now) is False
        assert _is_schedule_exempt({}, now) is False
        assert _is_schedule_exempt({"preassigned_slot": {}}, now) is False
        assert (
            _is_schedule_exempt(
                {"preassigned_slot": {"scheduled_at": "not-a-datetime"}}, now
            )
            is False
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
        assert cfg.stuck_threshold_sec == 3600
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
    """DB 路径测试：依赖 docker-compose 里的 PostgreSQL。

    测试约定：
    - 用 ``DATABASE_URL`` 环境变量判断是否有 PG；无则 skip。
    - 每个测试自建 engine + session factory，写入后立即 commit，
      保证 ``AgentRunScanner`` 内部新建的 session 能读到数据。
    """

    @pytest.fixture(autouse=True)
    def _require_pg(self):
        pg_url = os.environ.get("DATABASE_URL", "")
        if not pg_url.startswith(("postgres", "postgresql")):
            pytest.skip("requires DATABASE_URL=postgres://...")

    @pytest.fixture
    async def _session_factory(self, engine):
        """返回基于测试 engine 的 session maker（测试负责 commit）。"""
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return sm

    @pytest.mark.asyncio
    async def test_find_stuck_picks_old_running_only(
        self, engine, _session_factory
    ):
        from matrix.agent.watcher import AgentRunScanner
        from matrix.db.models import AgentCheckpoint, AgentRun, Business

        now = datetime.now(timezone.utc)
        threshold = 600
        cutoff = now - timedelta(seconds=threshold)

        scanner = AgentRunScanner(_session_factory)

        async with _session_factory() as session:
            business = Business(
                name="测试业务",
                slug=f"test-{uuid4().hex[:8]}",
                status="active",
            )
            session.add(business)
            await session.flush()
            business_id = business.id

            # 场景 1：老 run + 新鲜 checkpoint → 不判 stuck
            run_fresh = AgentRun(
                status="running",
                business_id=business_id,
                started_at=now - timedelta(hours=2),
            )
            session.add(run_fresh)
            await session.flush()
            session.add(
                AgentCheckpoint(
                    run_id=run_fresh.id,
                    ts=now - timedelta(seconds=30),  # 在阈值内
                    from_state="IDLE",
                    to_state="PUBLISH",
                )
            )

            # 场景 2：老 run + checkpoint 停更 → 判 stuck
            run_stale_cp = AgentRun(
                status="running",
                business_id=business_id,
                started_at=now - timedelta(hours=2),
            )
            session.add(run_stale_cp)
            await session.flush()
            session.add(
                AgentCheckpoint(
                    run_id=run_stale_cp.id,
                    ts=cutoff - timedelta(seconds=60),  # 超过阈值
                    from_state="IDLE",
                    to_state="PUBLISH",
                )
            )

            # 场景 3：老 run + 无 checkpoint → 按 started_at 判 stuck
            run_no_cp = AgentRun(
                status="running",
                business_id=business_id,
                started_at=cutoff - timedelta(seconds=60),
            )
            session.add(run_no_cp)

            # 对照：年轻 run + 无 checkpoint → 不判 stuck
            run_young = AgentRun(
                status="running",
                business_id=business_id,
                started_at=now - timedelta(seconds=30),
            )
            session.add(run_young)

            await session.commit()

        stuck = await scanner.find_stuck_runs(now, threshold)
        stuck_ids = set(stuck)

        assert run_fresh.id not in stuck_ids
        assert run_stale_cp.id in stuck_ids
        assert run_no_cp.id in stuck_ids
        assert run_young.id not in stuck_ids

    @pytest.mark.asyncio
    async def test_find_stuck_exempts_run_waiting_for_schedule(
        self, engine, _session_factory
    ):
        """修看门狗误杀：checkpoint 停更但 preassigned_slot.scheduled_at 在未来
        （publish_node 合法 sleep 等错峰发布，最长约 5h）→ 不判 stuck。"""
        from matrix.agent.watcher import AgentRunScanner
        from matrix.db.models import AgentCheckpoint, AgentRun, Business

        now = datetime.now(timezone.utc)
        threshold = 600
        cutoff = now - timedelta(seconds=threshold)

        scanner = AgentRunScanner(_session_factory)

        async with _session_factory() as session:
            business = Business(
                name="测试业务",
                slug=f"test-{uuid4().hex[:8]}",
                status="active",
            )
            session.add(business)
            await session.flush()

            # 场景 1：stale checkpoint + scheduled_at 在未来 → 豁免
            run_waiting = AgentRun(
                status="running",
                business_id=business.id,
                started_at=now - timedelta(hours=2),
                payload={
                    "preassigned_slot": {
                        "scheduled_at": (now + timedelta(hours=3)).isoformat()
                    }
                },
            )
            session.add(run_waiting)
            await session.flush()
            session.add(
                AgentCheckpoint(
                    run_id=run_waiting.id,
                    ts=cutoff - timedelta(seconds=60),  # checkpoint 已超阈值
                    from_state="IDLE",
                    to_state="PUBLISH",
                )
            )

            # 场景 2：stale checkpoint + scheduled_at 已过（宽限外）→ 仍判 stuck
            run_overdue = AgentRun(
                status="running",
                business_id=business.id,
                started_at=now - timedelta(hours=6),
                payload={
                    "preassigned_slot": {
                        "scheduled_at": (now - timedelta(hours=2)).isoformat()
                    }
                },
            )
            session.add(run_overdue)
            await session.flush()
            session.add(
                AgentCheckpoint(
                    run_id=run_overdue.id,
                    ts=cutoff - timedelta(seconds=60),
                    from_state="IDLE",
                    to_state="PUBLISH",
                )
            )

            await session.commit()

        stuck = await scanner.find_stuck_runs(now, threshold)
        stuck_ids = set(stuck)

        assert run_waiting.id not in stuck_ids
        assert run_overdue.id in stuck_ids

    @pytest.mark.asyncio
    async def test_mark_timeout_updates_status_and_ended_at(
        self, engine, _session_factory
    ):
        from matrix.agent.watcher import AgentRunScanner
        from matrix.db.models import AgentRun, Business

        now = datetime.now(timezone.utc)

        scanner = AgentRunScanner(_session_factory)

        async with _session_factory() as session:
            business = Business(
                name="测试业务",
                slug=f"test-{uuid4().hex[:8]}",
                status="active",
            )
            session.add(business)
            await session.flush()

            run = AgentRun(
                status="running",
                business_id=business.id,
                started_at=now - timedelta(hours=1),
            )
            session.add(run)
            await session.commit()
            run_id = run.id

        await scanner.mark_timeout(run_id, now, "test timeout")

        async with _session_factory() as session:
            refreshed = await session.get(AgentRun, run_id)
            assert refreshed.status == "timeout"
            assert refreshed.ended_at == now


# 提前 import os 给 gate 用
import os  # noqa: E402
