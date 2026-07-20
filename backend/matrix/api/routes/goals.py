"""目标 CRUD 端点。

创建目标后由 ``GoalOrchestratorWorker`` 推进 phase=PENDING → PREPARING → ... → DONE；
具体的 AgentRun（每篇笔记一条）由 orchestrator 的 ``_prepare_round`` 创建，
不再由本路由塞"启动种子"。

v0.7 业务内单一 active goal：一个业务同时只能有一个未结束（status='active' 且 phase != 'DONE'）
的目标，防止多目标抢设备/账号/日额度。
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db, resolve_active_business
from matrix.api.schemas import (
    Goal,
    GoalCreate,
    GoalListResponse,
    GoalRound,
    GoalRoundListResponse,
    GoalUpdate,
)
from matrix.db.models import Goal as GoalORM, GoalRound as GoalRoundORM
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
        phase=(g.phase or "PENDING"),  # type: ignore[arg-type]
        current_round=g.current_round or 1,
        max_rounds=g.max_rounds or 3,
        target_likes=g.target_likes or 500,
        notes_per_round=g.notes_per_round or 3,
        learning_summary=g.learning_summary,
        phase_updated_at=g.phase_updated_at,
        business_id=g.business_id,  # v0.7+ 业务归属
    )


def _round_to_schema(r: GoalRoundORM) -> GoalRound:
    return GoalRound(
        id=r.id,
        goal_id=r.goal_id,
        round_number=r.round_number,
        started_at=r.started_at,
        ended_at=r.ended_at,
        kpi_summary=dict(r.kpi_summary or {}),
        notes_created=r.notes_created,
        total_views=r.total_views,
        total_likes=r.total_likes,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


async def _has_active_goal(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    exclude_goal_id: uuid.UUID | None = None,
) -> bool:
    """查询业务内是否已有未结束的 active goal。

    未结束定义：status='active' 且 phase != 'DONE'。
    achieved/failed/cancelled 均为已结束，不会阻塞新 goal。
    """
    stmt = (
        select(GoalORM.id)
        .where(
            GoalORM.business_id == business_id,
            GoalORM.status == "active",
            GoalORM.phase != "DONE",
            GoalORM.deleted_at.is_(None),
        )
        .limit(1)
    )
    if exclude_goal_id is not None:
        stmt = stmt.where(GoalORM.id != exclude_goal_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row is not None


@router.get("", response_model=GoalListResponse)
async def list_goals(
    business_id: Optional[uuid.UUID] = Query(None, description="v0.7+ 业务过滤"),
    session: AsyncSession = Depends(get_db),
) -> GoalListResponse:
    stmt = (
        select(GoalORM)
        .where(GoalORM.deleted_at.is_(None))
        .order_by(GoalORM.created_at.desc())
    )
    if business_id:
        stmt = stmt.where(GoalORM.business_id == business_id)
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


@router.patch("/{goal_id}", response_model=Goal)
async def update_goal(
    goal_id: uuid.UUID,
    body: GoalUpdate,
    session: AsyncSession = Depends(get_db),
) -> Goal:
    """改目标 type / target / deadline / KPI 阈值（局部更新）。

    注意：
      - target 整体覆盖（不是 merge），调用方应传完整对象
      - 修改 type 通常意味着 goal 重新规划，不会重启已派发的 agent run
        （前端如需"重启"请 cancel 后 create 新 goal）
    """
    g = await session.get(GoalORM, goal_id)
    if g is None or g.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "goal not found")
    if body.type is not None:
        g.type = body.type
    if body.target is not None:
        g.target = body.target
    if body.deadline is not None:
        g.deadline = body.deadline
    if body.target_likes is not None:
        g.target_likes = body.target_likes
    if body.notes_per_round is not None:
        g.notes_per_round = body.notes_per_round
    if body.max_rounds is not None:
        g.max_rounds = body.max_rounds
    if body.status is not None:
        # v0.7 业务内单一 active goal：重新激活时检查是否已有其他 active goal
        if body.status == "active" and g.status != "active":
            if await _has_active_goal(session, g.business_id, exclude_goal_id=g.id):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "business already has an active goal; wait for it to finish or cancel it",
                )
        g.status = body.status  # type: ignore[arg-type]
    await session.flush()
    return _to_schema(g)


# v0.7：硬删（物理删 goal 这一行；notes/metrics/KB 通过 notes 没 FK goal 不被删；
# agent_runs/goal_rounds/plans/tasks 有 CASCADE 自动清）
@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_goal(
    goal_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> None:
    """物理删 goal。已发布的笔记 / 复盘 KB 不受影响（无 FK）。"""
    g = await session.get(GoalORM, goal_id)
    if g is None or g.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "goal not found")
    await session.delete(g)
    await session.commit()


# v0.7 第 1 期：列出某 goal 的所有轮次（含 KPI 汇总）
@router.get("/{goal_id}/rounds", response_model=GoalRoundListResponse)
async def list_goal_rounds(
    goal_id: uuid.UUID,
    business_id: Optional[uuid.UUID] = Query(
        None, description="v0.7+ 业务校验（跨业务访问视为 404）"
    ),
    session: AsyncSession = Depends(get_db),
) -> GoalRoundListResponse:
    # 先确认 goal 存在
    g = await session.get(GoalORM, goal_id)
    if g is None or g.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "goal not found")
    # v0.7+：跨业务访问 → 404（不暴露存在性）
    if business_id is not None and g.business_id != business_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "goal not found")
    stmt = (
        select(GoalRoundORM)
        .where(GoalRoundORM.goal_id == goal_id)
        .order_by(GoalRoundORM.round_number.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return GoalRoundListResponse(
        items=[_round_to_schema(r) for r in rows], total=len(rows)
    )


@router.post("", response_model=Goal, status_code=status.HTTP_201_CREATED)
async def create_goal(
    body: GoalCreate,
    session: AsyncSession = Depends(get_db),
) -> Goal:
    # v0.7+ 业务模型重构：业务上下文校验（存在 + active）
    await resolve_active_business(session, body.business_id)

    # v0.7 业务内单一 active goal：防止多目标抢设备/账号/日额度
    if await _has_active_goal(session, body.business_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "business already has an active goal; wait for it to finish or cancel it",
        )

    # target 接受 ThemeTarget 结构或 dict；统一存为 dict 给 JSONB
    target_dict = body.target if isinstance(body.target, dict) else dict(body.target or {})

    g = GoalORM(
        type=body.type,
        target=target_dict,
        deadline=body.deadline,
        status="active",
        # 可调字段（None 时用 DB default 500/3/3）
        target_likes=body.target_likes if body.target_likes is not None else 500,
        notes_per_round=body.notes_per_round if body.notes_per_round is not None else 3,
        max_rounds=body.max_rounds if body.max_rounds is not None else 3,
        business_id=body.business_id,  # v0.7+ 业务归属
    )
    session.add(g)
    await session.flush()

    # v0.7+ 第 2 期：去掉"启动种子 AgentRun"。
    # 旧的：create_goal 自己塞 1 条 IDLE AgentRun + 让 AgentRunWorker 拉起 RESEARCH → DRAFT...
    # 新的：goal 进入 phase=PENDING，由 GoalOrchestratorWorker 推进
    #       PENDING→PREPARING（_prepare_round）一次性建 N 条带 round_number 和 preassigned_slot 的 run。
    # 这样首轮跑出恰好 N 条（不再是 1+N），且全部带 round_number，orchestrator 按轮次查询能命中索引。

    logger.info(
        "goal.created",
        goal_id=str(g.id),
        type=g.type,
        notes_per_round=g.notes_per_round,
    )
    # 包裹一个 trace span（不阻塞主流程，失败仅记录）
    try:
        with trace_agent_run(str(g.id), goal=f"{g.type}:{g.id}"):
            pass
    except Exception:  # pragma: no cover - tracing 失败不影响业务
        logger.exception("trace_agent_run failed")

    return _to_schema(g)
