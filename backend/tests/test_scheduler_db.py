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
        # SQLAlchemy 2.x: stmt.compile() 返回 Compiled 对象，必须 str() 转字符串
        values = str(stmt.compile(compile_kwargs={"literal_binds": True}))
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
        # SQLAlchemy 2.x: stmt.compile() 返回 Compiled 对象，必须 str() 转字符串
        values = str(stmt.compile(compile_kwargs={"literal_binds": True}))
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
        # JSONB 列无内置 literal renderer；SQLAlchemy 2.x 默认渲染为 :param 占位符。
        # 改从 compile().params 取实际值检查，避免触发 JSONB literal render。
        params = stmt.compile().params
        assert params.get("status") == "failed"
        assert params.get("last_error") == {"code": "EXECUTOR_FALSE", "message": "x"}


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

    # ---- Phase 1 P1-1：_do_collect 持久化 + 通知 ----

    @pytest.mark.asyncio
    async def test_collect_persists_note_metric_and_updates_note(self):
        """采集成功：写 NoteMetric + 更新 Note.collected_at + 发 note.collected 通知。"""
        col = _FakeCollector(metrics={"views": 100, "likes": 10, "collects": 3})
        session_factory, session = _build_mock_session_factory()
        # session.get(Note, ...) 返回一个 mock note 对象
        mock_note = MagicMock()
        mock_note.collected_at = None
        mock_note.collected_run_id = None
        session.get = AsyncMock(return_value=mock_note)
        notifier_calls: list[tuple[str, dict]] = []

        async def fake_notifier(code: str, payload: dict) -> None:
            notifier_calls.append((code, payload))

        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=col,
            session_factory=session_factory,
            notifier=fake_notifier,
        )
        note_id = uuid4()
        task = _make_task_like(
            action="device_collect_metrics",
            payload={
                "platform_note_id": "p1",
                "scope": "recent_24h",
                "note_id": str(note_id),
                "goal_id": str(uuid4()),
                "run_id": str(uuid4()),
            },
        )
        assert await executor.execute(task) is True

        # NoteMetric 行被 add；Note 被 get + 字段被写
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        # NoteMetric 模型；检查字段
        assert added.__class__.__name__ == "NoteMetric"
        assert added.note_id == note_id
        assert added.views == 100
        assert added.likes == 10
        assert added.collects == 3
        # Note.collected_at 被赋值；collected_run_id 被设为 run_id
        assert mock_note.collected_at is not None
        # session.commit 被调（写库 + 通知都在 commit 之后）
        assert session.commit.await_count >= 1
        # 通知被发
        assert len(notifier_calls) == 1
        assert notifier_calls[0][0] == "note.collected"
        assert notifier_calls[0][1]["views"] == 100
        assert notifier_calls[0][1]["likes"] == 10

    @pytest.mark.asyncio
    async def test_collect_failure_emits_warning_notification(self):
        """采集失败：发 note.collect.failed 通知、return False。"""
        col = _FakeCollector(raise_exc=True)
        session_factory, _session = _build_mock_session_factory()
        notifier_calls: list[tuple[str, dict]] = []

        async def fake_notifier(code: str, payload: dict) -> None:
            notifier_calls.append((code, payload))

        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=col,
            session_factory=session_factory,
            notifier=fake_notifier,
        )
        note_id = uuid4()
        task = _make_task_like(
            action="device_collect_metrics",
            payload={"platform_note_id": "p1", "note_id": str(note_id)},
        )
        assert await executor.execute(task) is False
        assert len(notifier_calls) == 1
        assert notifier_calls[0][0] == "note.collect.failed"
        assert notifier_calls[0][1]["platform_note_id"] == "p1"

    @pytest.mark.asyncio
    async def test_collect_without_session_factory_returns_true_silently(self):
        """没注入 session_factory 时：拿到 metrics 就 return True，不持久化、不通知。
        （向后兼容旧调用方；新生产路径已传 session_factory）"""
        col = _FakeCollector(metrics={"views": 50})
        notifier_calls: list = []

        async def fake_notifier(code: str, payload: dict) -> None:
            notifier_calls.append(code)

        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(),
            device_collector=col,
            notifier=fake_notifier,  # 有 notifier 但没 session_factory
        )
        task = _make_task_like(
            action="device_collect_metrics",
            payload={"platform_note_id": "p1", "note_id": str(uuid4())},
        )
        assert await executor.execute(task) is True
        # 没 session_factory → 不写 DB → 不发通知
        assert notifier_calls == []

    # ---- Phase 2a #7：熔断器 ----

    @pytest.mark.asyncio
    async def test_circuit_open_skips_dispatch_and_notifies(self):
        """熔断打开时直接 return False，且发 agent.alert 通知。"""
        from matrix.scheduler.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(window=600, threshold=2, cool_off=60)
        breaker.record_failure()
        breaker.record_failure()  # 触发熔断
        assert breaker.is_open() is True

        notifier_calls: list[tuple[str, dict]] = []

        async def fake_notifier(code: str, payload: dict) -> None:
            notifier_calls.append((code, payload))

        pub = _FakePublisher(ok=True)
        col = _FakeCollector(metrics={"views": 10})
        executor = DeviceTaskExecutor(
            device_publisher=pub,
            device_collector=col,
            notifier=fake_notifier,
            breaker=breaker,
        )
        task = _make_task_like(
            action="device_publish",
            payload={"title": "t", "content": "c", "images": [], "tags": []},
        )
        # publisher/collector 不应被调用
        assert await executor.execute(task) is False
        assert pub.calls == []
        # 发了熔断告警
        assert len(notifier_calls) == 1
        assert notifier_calls[0][0] == "agent.alert"
        assert notifier_calls[0][1]["code"] == "CIRCUIT_OPEN"
        assert notifier_calls[0][1]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_failure_records_to_breaker(self):
        """publish 失败要喂给 breaker。"""
        from matrix.scheduler.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(window=600, threshold=10, cool_off=60)
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(ok=False, error_code="RISK_BLOCKED"),
            device_collector=_FakeCollector(),
            breaker=breaker,
        )
        task = _make_task_like(
            action="device_publish",
            payload={"title": "t", "content": "c", "images": [], "tags": []},
        )
        assert await executor.execute(task) is False
        assert len(breaker.failures) == 1
        # 没到 threshold → 还没熔
        assert breaker.is_open() is False

    @pytest.mark.asyncio
    async def test_success_does_not_record_to_breaker(self):
        """成功不喂 breaker。"""
        from matrix.scheduler.circuit_breaker import CircuitBreaker

        breaker = CircuitBreaker(window=600, threshold=10, cool_off=60)
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(ok=True),
            device_collector=_FakeCollector(),
            breaker=breaker,
        )
        task = _make_task_like(
            action="device_publish",
            payload={"title": "t", "content": "c", "images": [], "tags": []},
        )
        assert await executor.execute(task) is True
        assert breaker.failures == []

    @pytest.mark.asyncio
    async def test_unhandled_exception_records_failure_and_notifies(self):
        """适配器抛异常 → 记录失败 + 发 ERROR 通知，绝不传给 worker。"""
        from matrix.scheduler.circuit_breaker import CircuitBreaker

        class BoomPublisher:
            def __init__(self):
                self.calls = 0

            async def publish(self, **kw):
                self.calls += 1
                raise RuntimeError("apk timed out")

        breaker = CircuitBreaker(window=600, threshold=10, cool_off=60)
        notifier_calls: list[tuple[str, dict]] = []

        async def fake_notifier(code: str, payload: dict) -> None:
            notifier_calls.append((code, payload))

        pub = BoomPublisher()
        executor = DeviceTaskExecutor(
            device_publisher=pub,
            device_collector=_FakeCollector(),
            notifier=fake_notifier,
            breaker=breaker,
        )
        task = _make_task_like(action="device_publish", payload={"title": "t"})
        assert await executor.execute(task) is False
        # 喂给 breaker 一次
        assert len(breaker.failures) == 1
        # 发了一条 EXECUTOR_EXCEPTION 告警
        assert len(notifier_calls) == 1
        assert notifier_calls[0][0] == "agent.alert"
        assert notifier_calls[0][1]["code"] == "EXECUTOR_EXCEPTION"
        assert notifier_calls[0][1]["severity"] == "error"

    @pytest.mark.asyncio
    async def test_no_breaker_is_backward_compatible(self):
        """breaker=None 时：原行为不变（失败不喂任何东西）。"""
        executor = DeviceTaskExecutor(
            device_publisher=_FakePublisher(ok=False, error_code="X"),
            device_collector=_FakeCollector(),
        )
        task = _make_task_like(action="device_publish", payload={"title": "t"})
        # 单纯失败，没有 breaker、没有 notifier → 不会崩
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
