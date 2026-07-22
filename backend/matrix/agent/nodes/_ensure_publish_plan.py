"""Phase 1 P1-1：复用 goal 维度的 publish plan。

每个 goal 最多一个 plan 行（``steps.kind='publish'``），
约束由 ``uq_plans_publish`` partial unique index 强制。

publish_node 排 publish task 时复用同一 plan 行，避免每条笔记一条冗余 plan 行。
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Plan
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


async def ensure_publish_plan_id(
    session: AsyncSession, goal_id: Optional[uuid.UUID]
) -> uuid.UUID:
    """返回该 goal 对应的 publish plan 的 id；缺则创建。"""
    if goal_id is None:
        raise ValueError(
            "ensure_publish_plan_id requires goal_id; publish_node 必须在 EXECUTING 上下文"
        )

    existing = (
        await session.execute(
            select(Plan.id).where(
                Plan.goal_id == goal_id,
                Plan.steps["kind"].astext == "publish",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.commit()
        return existing

    new_id = uuid.uuid4()
    session.add(
        Plan(
            id=new_id,
            goal_id=goal_id,
            steps={"kind": "publish"},
            status="pending",
        )
    )
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (
            await session.execute(
                select(Plan.id).where(
                    Plan.goal_id == goal_id,
                    Plan.steps["kind"].astext == "publish",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise

    return new_id


__all__ = ["ensure_publish_plan_id"]
