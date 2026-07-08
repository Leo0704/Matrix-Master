"""Agent run 列表 + 取消。"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import AgentRun, AgentRunListResponse, OkResponse
from matrix.db.models import AgentRun as AgentRunORM
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/agent/runs", tags=["agent-runs"])


def _to_schema(r: AgentRunORM) -> AgentRun:
    return AgentRun(
        id=r.id,
        goal_id=r.goal_id,
        current_state=r.current_state,
        status=r.status,  # type: ignore[arg-type]
        started_at=r.started_at,
        updated_at=r.updated_at,
        ended_at=r.ended_at,
    )


@router.get("", response_model=AgentRunListResponse)
async def list_agent_runs(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
) -> AgentRunListResponse:
    stmt = select(AgentRunORM).order_by(AgentRunORM.started_at.desc())
    if status_filter:
        stmt = stmt.where(AgentRunORM.status == status_filter)
    stmt = stmt.limit(limit)
    rows = (await session.execute(stmt)).scalars().all()
    return AgentRunListResponse(items=[_to_schema(r) for r in rows])


@router.get("/{run_id}", response_model=AgentRun)
async def get_agent_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> AgentRun:
    r = await session.get(AgentRunORM, run_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent run not found")
    return _to_schema(r)


@router.post("/{run_id}/cancel", response_model=OkResponse)
async def cancel_agent_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> OkResponse:
    r = await session.get(AgentRunORM, run_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent run not found")
    if r.status != "running":
        # 幂等：已经结束的 run 视为「取消请求已接收」
        logger.info(
            "cancel called on non-running run", run_id=str(run_id), status=r.status
        )
        return OkResponse(ok=True)
    r.status = "cancelled"
    await session.flush()
    logger.info("agent run cancelled", run_id=str(run_id))
    return OkResponse(ok=True)
