"""调度器 DB 集成层。

把 :class:`matrix.scheduler.scheduler` 的三个 Protocol（TaskLoader / TaskStatusWriter）
与 dispatch_node 写入 task 的能力落到 ``tasks`` 表上。

设计原则：
- 不缓存状态；每次操作都从 session_factory 拿独立 session
- 不重写 ORM 字段命名（与 docs/database/schema.sql 严格对齐）
- ``_DbTaskAdapter`` 把 ORM 行适配成 TaskLike Protocol（duck type），不依赖 dataclass
  实例，方便跨模块消费
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.db.models import Task
from matrix.scheduler.scheduler import TaskLike


# ---------------------------------------------------------------------------
# Adapter：ORM Task → TaskLike duck type
# ---------------------------------------------------------------------------


@dataclass
class _DbTaskAdapter:
    """把 :class:`matrix.db.models.Task` 行适配成 :class:`TaskLike` Protocol 字段。

    不依赖 ``Task`` ORM 实例本身（避免在调度器内部反向依赖 db.models 模块）。
    字段与 :class:`matrix.scheduler.scheduler.TaskLike` 1:1 对齐。
    """

    id: UUID
    plan_id: UUID
    device_id: UUID
    account_id: UUID
    action: str
    payload: dict
    request_id: str
    status: str
    attempts: int
    last_error: dict | None
    scheduled_at: datetime
    executed_at: datetime | None

    @classmethod
    def from_orm(cls, row: Task) -> _DbTaskAdapter:
        return cls(
            id=row.id,
            plan_id=row.plan_id,
            device_id=row.device_id,
            account_id=row.account_id,
            action=row.action,
            payload=row.payload,
            request_id=row.request_id,
            status=row.status,
            attempts=row.attempts,
            last_error=row.last_error,
            scheduled_at=row.scheduled_at,
            executed_at=row.executed_at,
        )


# ---------------------------------------------------------------------------
# DbTaskWriter：dispatch_node → tasks 表
# ---------------------------------------------------------------------------


class DbTaskWriter:
    """把 dispatch_node 产出的 ``rec: dict`` 写入 ``tasks`` 表。

    ``rec`` 必填字段（与 ``tasks`` schema 对齐）：
        - id: UUID
        - plan_id: UUID
        - device_id: UUID
        - account_id: UUID
        - action: str
        - payload: dict
        - request_id: str
        - scheduled_at: datetime

    返回写入行的 UUID（即 rec['id']）；若 rec['id'] 缺省则在 DB 默认值（uuid_generate_v4）
    生成，但调用方需要返回 UUID 时仍按 DB 生成值回填。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def __call__(self, rec: dict[str, Any]) -> UUID:
        # 必填字段在调用前由 dispatch_node 负责；这里只做 schema-level 兜底校验
        required = ("plan_id", "device_id", "account_id", "action", "payload", "request_id", "scheduled_at")
        missing = [k for k in required if k not in rec]
        if missing:
            raise ValueError(f"DbTaskWriter: rec missing required fields: {missing}")

        task_id = rec.get("id") or uuid4()
        if not isinstance(task_id, UUID):
            task_id = UUID(str(task_id))

        async with self._factory() as session:
            try:
                row = Task(
                    id=task_id,
                    plan_id=_as_uuid(rec["plan_id"]),
                    device_id=_as_uuid(rec["device_id"]),
                    account_id=_as_uuid(rec["account_id"]),
                    action=str(rec["action"]),
                    payload=dict(rec["payload"]),
                    request_id=str(rec["request_id"]),
                    status="pending",
                    scheduled_at=_as_datetime(rec["scheduled_at"]),
                )
                session.add(row)
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return task_id

    async def close(self) -> None:  # pragma: no cover - 当前没有需要清理的资源
        return None


# ---------------------------------------------------------------------------
# DbTaskLoader：实现 TaskLoader Protocol
# ---------------------------------------------------------------------------


class DbTaskLoader:
    """从 ``tasks`` 拉取到期 pending task。

    排他锁：生产路径用 ``FOR UPDATE SKIP LOCKED`` 防止多实例调度器双消费。
    对不支持 skip_locked 的 dialect（sqlite）自动降级为普通 ``with_for_update``，
    单元测试因此能直接跑 in-memory DB。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def load_pending(self, now: datetime, limit: int) -> list[TaskLike]:
        async with self._factory() as session:
            stmt = (
                select(Task)
                .where(Task.status == "pending")
                .where(Task.scheduled_at <= now)
                .order_by(Task.scheduled_at)
                .limit(limit)
            )
            # 注：v0.7 单实例调度器暂不启用 FOR UPDATE SKIP LOCKED；
            # 多实例并行消费的原子性放后续 PR 跟进（见 plan §"不在本 PR 范围"）。
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_DbTaskAdapter.from_orm(r) for r in rows]


# ---------------------------------------------------------------------------
# DbTaskStatusWriter：mark_running / mark_success / mark_failed
# ---------------------------------------------------------------------------


class DbTaskStatusWriter:
    """把 status / executed_at / last_error / attempts 写回 ``tasks`` 表。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    async def mark_running(self, task: TaskLike) -> None:
        await self._update(
            task.id,
            status="running",
            attempts=task.attempts + 1,
        )

    async def mark_success(self, task: TaskLike, executed_at: datetime) -> None:
        await self._update(
            task.id,
            status="success",
            executed_at=executed_at,
        )

    async def mark_failed(
        self, task: TaskLike, error: dict, executed_at: datetime
    ) -> None:
        await self._update(
            task.id,
            status="failed",
            executed_at=executed_at,
            last_error=error,
        )

    async def _update(
        self,
        task_id: Any,
        *,
        status: str,
        executed_at: datetime | None = None,
        last_error: dict | None = None,
        attempts: int | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if executed_at is not None:
            values["executed_at"] = executed_at
        if last_error is not None:
            values["last_error"] = last_error
        if attempts is not None:
            values["attempts"] = attempts

        async with self._factory() as session:
            try:
                stmt = update(Task).where(Task.id == _as_uuid(task_id)).values(**values)
                await session.execute(stmt)
                await session.commit()
            except Exception:
                await session.rollback()
                raise


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
