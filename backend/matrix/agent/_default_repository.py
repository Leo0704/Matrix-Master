"""AgentRepository 的 default 实现：基于 ``matrix.db`` 的 SQLAlchemy ORM。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from matrix.db.models import AgentCheckpoint as AgentCheckpointModel
from matrix.db.models import AgentRun as AgentRunModel

from .checkpoint import (
    get_run as db_get_run,
)
from .checkpoint import (
    read_all_checkpoints as db_read_all,
)
from .checkpoint import (
    read_last_checkpoint as db_read_last,
)
from .checkpoint import (
    update_run_state as db_update_run,
)
from .checkpoint import (
    write_checkpoint as db_write_cp,
)
from .repository import AgentCheckpointRow, AgentRunRow

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _orm_to_run_row(row: AgentRunModel) -> AgentRunRow:
    class _Row:
        pass

    out = _Row()
    out.id = row.id
    out.goal_id = row.goal_id
    out.current_state = row.current_state
    out.payload = row.payload
    out.status = row.status
    out.started_at = row.started_at
    out.updated_at = row.updated_at
    out.ended_at = row.ended_at
    return out


def _orm_to_cp_row(row: AgentCheckpointModel) -> AgentCheckpointRow:
    class _Row:
        pass

    out = _Row()
    out.run_id = row.run_id
    out.ts = row.ts
    out.from_state = row.from_state
    out.to_state = row.to_state
    out.payload = row.payload
    return out


class DefaultAgentRepository:
    """基于 ``matrix.db.session.get_session`` 的生产实现。"""

    def __init__(self, session_factory=None) -> None:
        self._custom_session_factory = session_factory

    def _session(self) -> Any:
        if self._custom_session_factory is not None:
            return self._custom_session_factory()
        from matrix.db.session import get_session_factory

        return get_session_factory()()

    async def create_run(
        self,
        *,
        run_id: UUID,
        goal_id: UUID | None,
        payload: dict[str, Any],
        started_at: datetime,
        current_state: str,
        status: str,
    ) -> None:
        async with self._session() as session:  # type: AsyncSession
            row = AgentRunModel(
                id=run_id,
                goal_id=goal_id,
                current_state=current_state,
                payload=payload,
                status=status,
                started_at=started_at,
            )
            session.add(row)
            await db_write_cp(
                session,
                run_id=run_id,
                from_state=current_state,
                to_state=current_state,
                payload={"created": True},
            )

    async def write_checkpoint(
        self,
        *,
        run_id: UUID,
        from_state: str,
        to_state: str,
        payload: dict[str, Any] | None,
        ts: datetime | None = None,
    ) -> None:
        async with self._session() as session:
            await db_write_cp(
                session,
                run_id=run_id,
                from_state=from_state,
                to_state=to_state,
                payload=payload,
                ts=ts,
            )

    async def read_last_checkpoint(
        self, run_id: UUID
    ) -> AgentCheckpointRow | None:
        async with self._session() as session:
            row = await db_read_last(session, run_id)
            return _orm_to_cp_row(row) if row else None

    async def read_all_checkpoints(
        self, run_id: UUID
    ) -> list[AgentCheckpointRow]:
        async with self._session() as session:
            rows = await db_read_all(session, run_id)
            return [_orm_to_cp_row(r) for r in rows]

    async def get_run(self, run_id: UUID) -> AgentRunRow | None:
        async with self._session() as session:
            row = await db_get_run(session, run_id)
            return _orm_to_run_row(row) if row else None

    async def update_run(
        self,
        run_id: UUID,
        *,
        current_state: str | None = None,
        status: str | None = None,
        payload_merge: dict[str, Any] | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        async with self._session() as session:
            await db_update_run(
                session,
                run_id=run_id,
                current_state=current_state,
                status=status,
                payload=payload_merge,
                ended_at=ended_at,
            )


__all__ = ["DefaultAgentRepository"]
