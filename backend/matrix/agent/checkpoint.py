"""Checkpoint 持久化。

每次状态机转移写入一条 ``agent_checkpoints`` 记录。``resume_run`` 通过
``read_last_checkpoint`` 拿到最近转移 + payload 重放 state。
"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import AgentCheckpoint, AgentRun

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# 写入 / 读取
# ---------------------------------------------------------------------------


async def write_checkpoint(
    session: AsyncSession,
    *,
    run_id: UUID,
    from_state: str,
    to_state: str,
    payload: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> AgentCheckpoint:
    """向 ``agent_checkpoints`` 表写入一条转移记录。"""
    cp = AgentCheckpoint(
        run_id=run_id,
        ts=ts or _utcnow(),
        from_state=from_state,
        to_state=to_state,
        payload=payload,
    )
    session.add(cp)
    try:
        await session.flush()
    except SQLAlchemyError:
        await session.rollback()
        raise
    return cp


async def read_last_checkpoint(
    session: AsyncSession, run_id: UUID
) -> AgentCheckpoint | None:
    """读取最近一次 checkpoint（按 ts desc）。"""
    stmt = (
        select(AgentCheckpoint)
        .where(AgentCheckpoint.run_id == run_id)
        .order_by(desc(AgentCheckpoint.ts))
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def read_all_checkpoints(
    session: AsyncSession, run_id: UUID
) -> list[AgentCheckpoint]:
    """读取一次 run 的全部 checkpoint（按 ts asc）。"""
    stmt = (
        select(AgentCheckpoint)
        .where(AgentCheckpoint.run_id == run_id)
        .order_by(AgentCheckpoint.ts)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# run 续跑
# ---------------------------------------------------------------------------


async def resume_run(session: AsyncSession, run_id: UUID) -> dict[str, Any] | None:
    """从最后一条 checkpoint 还原 state（payload + to_state）。

    Returns:
        ``None`` if no checkpoint found; else ``{"run_id", "to_state", "payload", "ts"}``.
    """
    cp = await read_last_checkpoint(session, run_id)
    if cp is None:
        return None
    return {
        "run_id": str(cp.run_id),
        "to_state": cp.to_state,
        "payload": cp.payload or {},
        "ts": cp.ts,
    }


# ---------------------------------------------------------------------------
# run row helpers
# ---------------------------------------------------------------------------


async def update_run_state(
    session: AsyncSession,
    *,
    run_id: UUID,
    current_state: str | None = None,
    status: str | None = None,
    payload: dict[str, Any] | None = None,
    ended_at: datetime | None = None,
) -> None:
    """更新 ``agent_runs`` 行的若干字段。``None`` 表示该字段不动。"""
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise ValueError(f"agent_run not found: {run_id}")
    if current_state is not None:
        run.current_state = current_state
    if status is not None:
        run.status = status
    if payload is not None:
        # 合并到现有 payload
        merged = dict(run.payload or {})
        merged.update(payload)
        run.payload = merged
    if ended_at is not None:
        run.ended_at = ended_at
    run.updated_at = _utcnow()
    await session.flush()


async def get_run(session: AsyncSession, run_id: UUID) -> AgentRun | None:
    return await session.get(AgentRun, run_id)


async def list_running_runs(session: AsyncSession) -> list[AgentRun]:
    stmt = select(AgentRun).where(AgentRun.status == "running")
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# 同步接口（用于测试或非 async caller）
# ---------------------------------------------------------------------------


# 故意不导出 async_to_sync 包装层；测试与生产都走 async session。
# 这里保留一个 factory helper 用于测试时构造内存 session：


def make_test_session_factory():
    """不在 production 用；保留接口以便未来提供 in-memory fixture。"""
    raise NotImplementedError("use matrix.db.session.get_session_factory() instead")


__all__ = [
    "write_checkpoint",
    "read_last_checkpoint",
    "read_all_checkpoints",
    "resume_run",
    "update_run_state",
    "get_run",
    "list_running_runs",
    "make_test_session_factory",
]
