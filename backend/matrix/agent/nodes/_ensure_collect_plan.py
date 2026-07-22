"""Phase 1 P1-1：复用 goal 维度的 post_publish_collect plan。

每个 goal 最多一个 plan 行（``steps.kind='post_publish_collect'``），
约束由 ``uq_plans_post_publish_collect`` partial unique index 强制。

publish_node 排 24h 采集 task 时复用同一 plan，避免每条笔记一条冗余 plan 行。
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Plan
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


async def ensure_collect_plan_id(
    session: AsyncSession, goal_id: Optional[uuid.UUID]
) -> uuid.UUID:
    """返回该 goal 对应的 post_publish_collect plan 的 id；缺则创建。

    ON CONFLICT DO NOTHING + 二次 SELECT 兜底（partial unique index 触发时
    第二次 SELECT 一定能命中）。
    """
    if goal_id is None:
        raise ValueError(
            "ensure_collect_plan_id requires goal_id; publish_node 必须在 EXECUTING 上下文"
        )

    # ON CONFLICT 走 uq_plans_post_publish_collect partial unique index。
    # 注意：partial unique index 不被 ON CONFLICT 直接支持（Postgres 限制），
    # 所以走两步法：先 SELECT 现有；没有再裸 INSERT，并发冲突交给
    # partial unique index 抛错 → except 分支回滚后重查。
    new_id = uuid.uuid4()
    stmt = pg_insert(Plan).values(
        id=new_id,
        goal_id=goal_id,
        steps={"kind": "post_publish_collect"},
        status="pending",
    )
    existing = (
        await session.execute(
            select(Plan.id).where(
                Plan.goal_id == goal_id,
                Plan.steps["kind"].astext == "post_publish_collect",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await session.commit()
        return existing
    try:
        await session.execute(stmt)
        await session.commit()
        # 检查插入是否成功（pg_insert 不带 RETURNING + DO NOTHING 时拿不到 rowcount 干净值，
        # 保险起见重查一次）
        row = (
            await session.execute(
                select(Plan.id).where(
                    Plan.goal_id == goal_id,
                    Plan.steps["kind"].astext == "post_publish_collect",
                )
            )
        ).scalar_one()
        return row
    except Exception:
        await session.rollback()
        # 并发情况下别人先插入了，回退到 SELECT
        existing = (
            await session.execute(
                select(Plan.id).where(
                    Plan.goal_id == goal_id,
                    Plan.steps["kind"].astext == "post_publish_collect",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise


__all__ = ["ensure_collect_plan_id"]