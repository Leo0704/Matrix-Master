"""matrix.scheduler.db + matrix.scheduler.db_task_executor 单元测试。

不连真实 DB：session 用 ``AsyncMock`` 验证 SQL 构造与字段映射；
dispatch 走 ``DeviceTaskExecutor`` 时用 fake 协议。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from matrix.agent.protocols import InteractResult, PublishResult
from matrix.scheduler.db import (
    DbTaskLoader,
    DbTaskStatusWriter,
    DbTaskWriter,
    _DbTaskAdapter,
)
from matrix.scheduler.db_task_executor import DeviceTaskExecutor
from matrix.scheduler.scheduler import TaskLike


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_session_factory(mock_session: AsyncMock) -> Any:
    """构造一个 fake async_sessionmaker，使用传入的 mock session。"""

    @asynccontextmanager
    async def factory():
        yield mock_session

    return factory


def _build_mock_session_factory() -> tuple[Any, AsyncMock]:
    """构造（factory, session）对，session.execute/scalars/add 都被 mock。"""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.execute = AsyncMock()
    return _make_session_factory(session), session


def _make_task_like(
    *,
    action: str = "device_publish",
    payload: dict | None = None,
) -> TaskLike:
    """结构上满足 TaskLike Protocol 的最小对象。"""

    @dataclass
    class _T:
        id: UUID = field(default_factory=uuid4)
        plan_id: UUID = field(default_factory=uuid4)
        device_id: UUID = field(default_factory=uuid4)
        account_id: UUID = field(default_factory=uuid4)
        action: str = "device_publish"
        payload: dict = field(default_factory=dict)
        request_id: str = field(default_factory=lambda: str(uuid4()))
        status: str = "pending"
        attempts: int = 0
        last_error: dict | None = None
        scheduled_at: datetime = field(
            default_factory=lambda: datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
        )
        executed_at: datetime | None = None

    return _T(action=action, payload=payload or {})


# ---------------------------------------------------------------------------
# DbTaskWriter
# ---------------------------------------------------------------------------


class TestDbTaskWriter:
    @pytest.mark.asyncio
    async def test_inserts_task_with_all_fields(self):
        factory, session = _build_mock_session_factory()
        writer = DbTaskWriter(factory)

        rec = {
            "id": uuid4(),
            "plan_id": uuid4(),
            "device_id": uuid4(),
            "account_id": uuid4(),
            "action": "device_publish",
            "payload": {"title": "t", "content": "c", "images": [], "tags": []},
            "request_id": str(uuid4()),
            "scheduled_at": "2026-07-09T12:00:00+00:00",
        }
        result_id = await writer(rec)

        assert result_id == rec["id"]
        session.add.assert_called_once()
        row = session.add.call_args.args[0]
        assert row.action == "device_publish"
        assert row.status == "pending"
        assert row.payload == rec["payload"]
        assert row.request_id == rec["request_id"]
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generates_id_when_missing(self):
        factory, session = _build_mock_session_factory()
        writer = DbTaskWriter(factory)

        rec = {
            "plan_id": uuid4(),
            "device_id": uuid4(),
            "account_id": uuid4(),
            "action": "device_like",
            "payload": {"target_note_id": "n1"},
            "request_id": str(uuid4()),
            "scheduled_at": datetime.now(timezone.utc),
        }
        result_id = await writer(rec)
        assert isinstance(result_id, UUID)

    @pytest.mark.asyncio
    async def test_raises_on_missing_required_fields(self):
        factory, _ = _build_mock_session_factory()
        writer = DbTaskWriter(factory)
        with pytest.raises(ValueError, match="missing required fields"):
            await writer({"action": "device_publish"})

    @pytest.mark.asyncio
    async def test_rollback_on_db_error(self):
        factory, session = _build_mock_session_factory()
        session.add.side_effect = RuntimeError("db down")
        writer = DbTaskWriter(factory)

        rec = {
            "id": uuid4(),
            "plan_id": uuid4(),
            "device_id": uuid4(),
            "account_id": uuid4(),
            "action": "device_publish",
            "payload": {},
            "request_id": str(uuid4()),
            "scheduled_at": datetime.now(timezone.utc),
        }
        with pytest.raises(RuntimeError, match="db down"):
            await writer(rec)
        session.rollback.assert_awaited_once()


# ---------------------------------------------------------------------------
# DbTaskLoader
# ---------------------------------------------------------------------------


def _make_orm_row(*, status: str = "pending", scheduled_at: datetime | None = None) -> MagicMock:
    row = MagicMock()
    row.id = uuid4()
    row.plan_id = uuid4()
    row.device_id = uuid4()
    row.account_id = uuid4()
    row.action = "device_publish"
    row.payload = {"title": "t"}
    row.request_id = str(uuid4())
    row.status = status
    row.attempts = 0
    row.last_error = None
    row.scheduled_at = scheduled_at or datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    row.executed_at = None
    return row


class TestDbTaskLoader:
    @pytest.mark.asyncio
    async def test_loads_pending_and_adapts(self):
        factory, session = _build_mock_session_factory()
        row = _make_orm_row()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [row]
        session.execute.return_value = result

        loader = DbTaskLoader(factory)
        tasks = await loader.load_pending(datetime.now(timezone.utc), 10)

        assert len(tasks) == 1
        # 适配器字段对齐 TaskLike
        assert tasks[0].id == row.id
        assert tasks[0].action == "device_publish"
        assert tasks[0].payload == {"title": "t"}
        # 验证 SQL 构造里包含 status / scheduled_at
        stmt = session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "status" in compiled
        assert "scheduled_at" in compiled

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self):
        factory, session = _build_mock_session_factory()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute.return_value = result
        loader = DbTaskLoader(factory)
        assert await loader.load_pending(datetime.now(timezone.utc), 10) == []


# ---------------------------------------------------------------------------
# DbTaskStatusWriter
# ---------------------------------------------------------------------------


class TestDbTaskStatusWriter:
    @pytest.mark.asyncio
    async def test_mark_running_increments_attempts(self):
        factory, session = _build_mock_session_factory()
        writer = DbTaskStatusWriter(factory)
        task = _make_task_like()

        await writer.mark_running(task)

        stmt = session.execute.call_args.args[0]
        values = stmt.compile(compile_kwargs={"literal_binds": True})
        assert "status" in values
        assert "attempts" in values
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_success_sets_executed_at(self):
        factory, session = _build_mock_session_factory()
        writer = DbTaskStatusWriter(factory)
        task = _make_task_like()
        now = datetime.now(timezone.utc)
        await writer.mark_success(task, now)
        stmt = session.execute.call_args.args[0]
        values = stmt.compile(compile_kwargs={"literal_binds": True})
        assert "success" in values
        assert "executed_at" in values

    @pytest.mark.asyncio
    async def test_mark_failed_sets_status_and_error(self):
        factory, session = _build_mock_session_factory()
        writer = DbTaskStatusWriter(factory)
        task = _make_task_like()
        now = datetime.now(timezone.utc)
        await writer.mark_failed(task, {"code": "EXECUTOR_FALSE", "message": "x"}, now)
        stmt = session.execute.call_args.args[0]
        values = stmt.compile(compile_kwargs={"literal_binds": True})
        assert "failed" in values
        assert "last_error" in values


# ---------------------------------------------------------------------------
# DeviceTaskExecutor
# ---------------------------------------------------------------------------


class _FakePublisher:
    def __init__(self, ok: bool = True, error_code: str | None = None) -> None:
        self.ok = ok
        self.error_code = error_code
        self.calls: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> PublishResult:
        self.calls.append(kwargs)
        return PublishResult(
            ok=self.ok,
            note_id=uuid4(),
            error_code=self.error_code,
        )


class _FakeCollector:
    def __init__(self, metrics: dict | None = None, raise_exc: bool = False) -> None:
        self.metrics = metrics or {"views": 0, "likes": 0}
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    async def collect(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        if self.raise_exc:
            raise RuntimeError("collect failed")
        return self.metrics


class _FakeInteractor:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[dict[str, Any]] = []

    async def interact(self, **kwargs: Any) -> InteractResult:
        self.calls.append(kwargs)
        return InteractResult(ok=self.ok, interaction_id=uuid4())


class TestDeviceTaskExecutor:
    @pytest.mark.asyncio
    async def test_dispatches_publish(self):
        pub = _FakePublisher(ok=True)
        executor = DeviceTaskExecutor(
            device_publisher=pub, device_collector=_FakeCollector()
        )
        task = _make_task_like(
            action="device_publish",
            payload={"title": "t", "content": "c", "images": [], "tags": ["a"]},
        )
        assert await executor.execute(task) is True
        assert pub.calls[0]["title"] == "t"
        assert pub.calls[0]["tags"] == ["a"]

    @pytest.mark.asyncio
    async def test_dispatches_publish_false(self):
        pub = _FakePublisher(ok=False, error_code="RISK_BLOCKED")
        executor = DeviceTaskExecutor(
            device_publisher=pub, device_collector=_FakeCollector()
        )
        task = _make_task_like(action="device_publish", payload={"title": "t"})
        assert await executor.execute(task) is False

    @pytest.mark.asyncio
    async def test_dispatches_collect_metrics(self):
        col = _FakeCollector(metrics={"views": 100, "likes": 10})
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=col,
        )
        task = _make_task_like(
            action="device_collect_metrics",
            payload={"platform_note_id": "p1", "scope": "recent_7d"},
        )
        assert await executor.execute(task) is True
        assert col.calls[0]["platform_note_id"] == "p1"
        assert col.calls[0]["scope"] == "recent_7d"

    @pytest.mark.asyncio
    async def test_collect_metrics_returns_false_on_exception(self):
        col = _FakeCollector(raise_exc=True)
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=col,
        )
        task = _make_task_like(action="device_collect_metrics", payload={})
        assert await executor.execute(task) is False

    @pytest.mark.asyncio
    async def test_dispatches_like_to_interactor(self):
        inter = _FakeInteractor(ok=True)
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=_FakeCollector(),
            device_interactor=inter,
        )
        task = _make_task_like(
            action="device_like",
            payload={"target_note_id": "n1"},
        )
        assert await executor.execute(task) is True
        assert inter.calls[0]["action"] == "like"
        assert inter.calls[0]["target_note_id"] == "n1"

    @pytest.mark.asyncio
    async def test_dispatches_comment_to_interactor(self):
        inter = _FakeInteractor(ok=False)
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=_FakeCollector(),
            device_interactor=inter,
        )
        task = _make_task_like(
            action="device_comment",
            payload={"target_note_id": "n1", "content": "good"},
        )
        assert await executor.execute(task) is False
        assert inter.calls[0]["action"] == "comment"
        assert inter.calls[0]["content"] == "good"

    @pytest.mark.asyncio
    async def test_interact_without_interactor_returns_false(self):
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=_FakeCollector(),
            device_interactor=None,
        )
        task = _make_task_like(action="device_like", payload={"target_note_id": "n1"})
        assert await executor.execute(task) is False

    @pytest.mark.asyncio
    async def test_unknown_action_returns_false(self):
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=_FakeCollector(),
        )
        task = _make_task_like(action="device_teleport", payload={})
        assert await executor.execute(task) is False


# ---------------------------------------------------------------------------
# _DbTaskAdapter
# ---------------------------------------------------------------------------


class TestDbTaskAdapter:
    def test_from_orm_copies_all_fields(self):
        row = _make_orm_row()
        adapter = _DbTaskAdapter.from_orm(row)
        assert adapter.id == row.id
        assert adapter.plan_id == row.plan_id
        assert adapter.device_id == row.device_id
        assert adapter.account_id == row.account_id
        assert adapter.action == row.action
        assert adapter.payload == row.payload
        assert adapter.status == row.status
        assert adapter.attempts == row.attempts
        assert adapter.scheduled_at == row.scheduled_at
