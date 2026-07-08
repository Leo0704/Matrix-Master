"""目标 CRUD 端点。

创建目标会触发 Agent run：插入 AgentRun 行（status=running, current_state=IDLE），
由 matrix.agent 集成层（调度器或独立 worker）拉起 LangGraph 状态机。
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import Goal, GoalCreate, GoalListResponse
from matrix.db.models import AgentRun, Goal as GoalORM
from matrix.monitoring.logging import get_logger
from matrix.monitoring.tracing import trace_agent_run

logger = get_logger(__name__)

router = APIRouter(prefix="/goals", tags=["goals"])


def _to_schema(g: GoalORM) -> Goal:
    return Goal(
        id=g.id,
        type=g.type,
        target=dict(g.target or {}),
        deadline=g.deadline,
        status=g.status,  # type: ignore[arg-type]
    )


@router.get("", response_model=GoalListResponse)
async def list_goals(
    session: AsyncSession = Depends(get_db),
) -> GoalListResponse:
    stmt = (
        select(GoalORM)
        .where(GoalORM.deleted_at.is_(None))
        .order_by(GoalORM.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return GoalListResponse(items=[_to_schema(r) for r in rows])


@router.get("/{goal_id}", response_model=Goal)
async def get_goal(
    goal_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Goal:
    g = await session.get(GoalORM, goal_id)
    if g is None or g.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "goal not found")
    return _to_schema(g)


@router.post("", response_model=Goal, status_code=status.HTTP_201_CREATED)
async def create_goal(
    body: GoalCreate,
    session: AsyncSession = Depends(get_db),
) -> Goal:
    # target 接受 ThemeTarget 结构或 dict；统一存为 dict 给 JSONB
    target_dict = body.target if isinstance(body.target, dict) else dict(body.target or {})

    g = GoalORM(
        type=body.type,
        target=target_dict,
        deadline=body.deadline,
        status="active",
    )
    session.add(g)
    await session.flush()

    # 触发 Agent run：插入初始 row，让 matrix.agent 集成层接管
    # payload 含 brief（结构化主题）+ entry 起点，让 RunManager.start_run 注入 state["brief"]
    run = AgentRun(
        goal_id=g.id,
        current_state="IDLE",
        payload={
            "brief": target_dict,
            "entry": "RESEARCH",
        },
        status="running",
    )
    session.add(run)
    await session.flush()

    logger.info(
        "goal created, agent.run scheduled",
        goal_id=str(g.id),
        run_id=str(run.id),
        type=g.type,
    )
    # 包裹一个 trace span（不阻塞主流程，失败仅记录）
    try:
        with trace_agent_run(str(run.id), goal=f"{g.type}:{g.id}"):
            pass
    except Exception:  # pragma: no cover - tracing 失败不影响业务
        logger.warning("trace_agent_run failed", exc_info=True)

    return _to_schema(g)
