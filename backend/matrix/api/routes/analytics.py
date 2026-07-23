"""分析 / 图表端点：账号内容表现聚合（数据看板用）。

- GET /analytics/account-content-stats   每个账号的笔记数 + 已发布 + 草稿 + 平均曝光点赞评论
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    AccountContentStats,
    AccountContentStatsResponse,
    BusinessComparisonResponse,
    BusinessComparisonRow,
)
from matrix.db.models import (
    Account,
    AgentRun,
    Business,
    Device,
    Goal,
    KbDocument,
    Note,
    NoteMetric,
)
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# 账号内容表现（数据看板核心指标）
# ---------------------------------------------------------------------------


# note_metrics 是时序表（PK = note_id + ts），按 note_id 取最新一行
# 用 DISTINCT ON（PostgreSQL 专属）比窗口函数简单
_LATEST_METRIC_PER_NOTE = (
    select(
        NoteMetric.note_id.label("note_id"),
        NoteMetric.views.label("views"),
        NoteMetric.likes.label("likes"),
        NoteMetric.collects.label("collects"),
        NoteMetric.comments.label("comments"),
        func.row_number()
        .over(partition_by=NoteMetric.note_id, order_by=NoteMetric.ts.desc())
        .label("rn"),
    )
    .subquery()
)


@router.get("/account-content-stats", response_model=AccountContentStatsResponse)
async def account_content_stats(
    business_id: Optional[uuid.UUID] = Query(
        None,
        description=(
            "v0.7+ 业务过滤（schema 占位，暂未实际过滤；"
            "聚合查询 JOIN 多表，TODO 后续重构）"
        ),
    ),
    session: AsyncSession = Depends(get_db),
) -> AccountContentStatsResponse:
    """每个账号的内容表现聚合 + 未分配草稿池。

    逻辑：
      - 主表 LEFT JOIN notes + LEFT JOIN note_metrics 最新行
      - 按账号聚合：total / published / draft / scheduled + 三个均值（仅 published 计入）
      - 另起一行展示 account_id IS NULL 的草稿（DRAFT 节点落库时账号未定）
      - 按 published DESC, total_notes DESC 排序
    """
    latest = _LATEST_METRIC_PER_NOTE
    latest_latest = select(
        latest.c.note_id,
        latest.c.views,
        latest.c.likes,
        latest.c.collects,
        latest.c.comments,
    ).where(latest.c.rn == 1).subquery()

    # 1) 已分配账号的聚合（LEFT JOIN devices 拿关联设备昵称，严格 1 机 1 账号下有且仅有一个）
    assigned_stmt = (
        select(
            Account.id.label("account_id"),
            Account.handle.label("handle"),
            Account.status.label("status"),
            Device.nickname.label("device_nickname"),
            func.count(Note.id).label("total_notes"),
            func.sum(case((Note.status == "published", 1), else_=0)).label("published"),
            func.sum(case((Note.status == "draft", 1), else_=0)).label("draft"),
            func.sum(case((Note.status == "scheduled", 1), else_=0)).label("scheduled"),
            func.coalesce(
                func.avg(
                    case((Note.status == "published", latest_latest.c.views))
                ),
                0.0,
            ).label("avg_views"),
            func.coalesce(
                func.avg(
                    case((Note.status == "published", latest_latest.c.likes))
                ),
                0.0,
            ).label("avg_likes"),
            func.coalesce(
                func.avg(
                    case((Note.status == "published", latest_latest.c.comments))
                ),
                0.0,
            ).label("avg_comments"),
        )
        .select_from(Account)
        .outerjoin(
            Note,
            (Note.account_id == Account.id) & (Note.deleted_at.is_(None)),
        )
        .outerjoin(latest_latest, latest_latest.c.note_id == Note.id)
        .outerjoin(
            Device,
            (Device.id == Account.device_id) & (Device.deleted_at.is_(None)),
        )
        .where(Account.deleted_at.is_(None))
        .group_by(Account.id, Account.handle, Account.status, Device.nickname)
        .order_by(
            func.sum(case((Note.status == "published", 1), else_=0)).desc(),
            func.count(Note.id).desc(),
            Account.handle.asc(),
        )
    )
    assigned_rows = (await session.execute(assigned_stmt)).all()

    items: list[AccountContentStats] = [
        AccountContentStats(
            account_id=str(row.account_id) if row.account_id else None,
            handle=row.handle,
            status=row.status,
            device_nickname=row.device_nickname,
            total_notes=int(row.total_notes or 0),
            published=int(row.published or 0),
            draft=int(row.draft or 0),
            scheduled=int(row.scheduled or 0),
            avg_views=float(row.avg_views or 0.0),
            avg_likes=float(row.avg_likes or 0.0),
            avg_comments=float(row.avg_comments or 0.0),
        )
        for row in assigned_rows
    ]

    # 2) 未分配草稿池（account_id IS NULL 的笔记）— DRAFT 阶段先落库没绑账号的那批
    unassigned_stmt = select(func.count(Note.id)).where(
        Note.account_id.is_(None), Note.deleted_at.is_(None)
    )
    unassigned_total = int((await session.execute(unassigned_stmt)).scalar_one() or 0)
    if unassigned_total > 0:
        items.append(
            AccountContentStats(
                account_id=None,
                handle="(未分配草稿)",
                status="unassigned",
                total_notes=unassigned_total,
                published=0,
                draft=unassigned_total,
                scheduled=0,
                avg_views=0.0,
                avg_likes=0.0,
                avg_comments=0.0,
            )
        )

    return AccountContentStatsResponse(items=items)


# ---------------------------------------------------------------------------
# v0.7+ 多业务对比（dashboard 第 4 期）
# ---------------------------------------------------------------------------


@router.get("/business-comparison", response_model=BusinessComparisonResponse)
async def business_comparison(
    status_filter: Optional[str] = Query(
        None, alias="status", description="active / archived（不传返全部）"
    ),
    session: AsyncSession = Depends(get_db),
) -> BusinessComparisonResponse:
    """列出所有业务 × 9 个核心资源计数（用于 dashboard 对比）。

    一次 SQL group by 拿所有计数；不在 group by 的表用单条子查询。
    """
    # 业务列表
    biz_stmt = select(Business)
    if status_filter:
        biz_stmt = biz_stmt.where(Business.status == status_filter)
    biz_stmt = biz_stmt.order_by(Business.created_at.asc())
    businesses = (await session.execute(biz_stmt)).scalars().all()

    if not businesses:
        return BusinessComparisonResponse(items=[], total_businesses=0)

    biz_ids = [b.id for b in businesses]

    # 一次 group by 拿各表计数（排除软删）
    async def count(table, biz_col: str = "business_id", exclude_disabled: bool = False) -> dict[uuid.UUID, int]:
        col = getattr(table, biz_col)
        stmt = (
            select(col, func.count(table.id))
            .where(col.in_(biz_ids))
            .where(table.deleted_at.is_(None))
        )
        if exclude_disabled:
            stmt = stmt.where(table.status != "disabled")
        stmt = stmt.group_by(col)
        rows = (await session.execute(stmt)).all()
        return {row[0]: row[1] for row in rows}

    devices_count = await count(Device, exclude_disabled=True)
    accounts_count = await count(Account)
    goals_count = await count(Goal)
    notes_count = await count(Note)
    kb_count = await count(KbDocument)

    # agent_runs 没有 deleted_at
    ar_stmt = (
        select(AgentRun.business_id, func.count(AgentRun.id))
        .where(AgentRun.business_id.in_(biz_ids))
        .group_by(AgentRun.business_id)
    )
    runs_rows = (await session.execute(ar_stmt)).all()
    runs_count = {r[0]: r[1] for r in runs_rows}

    # published_notes（status='published'）
    pub_stmt = (
        select(Note.business_id, func.count(Note.id))
        .where(
            Note.business_id.in_(biz_ids),
            Note.deleted_at.is_(None),
            Note.status == "published",
        )
        .group_by(Note.business_id)
    )
    pub_rows = (await session.execute(pub_stmt)).all()
    published_count = {r[0]: r[1] for r in pub_rows}

    # successful_runs
    sr_stmt = (
        select(AgentRun.business_id, func.count(AgentRun.id))
        .where(
            AgentRun.business_id.in_(biz_ids),
            AgentRun.status == "success",
        )
        .group_by(AgentRun.business_id)
    )
    sr_rows = (await session.execute(sr_stmt)).all()
    success_count = {r[0]: r[1] for r in sr_rows}

    items = []
    for b in businesses:
        notes_n = notes_count.get(b.id, 0)
        accounts_n = accounts_count.get(b.id, 0)
        items.append(
            BusinessComparisonRow(
                business_id=b.id,
                business_name=b.name,
                business_slug=b.slug,
                status=b.status,  # type: ignore[arg-type]
                devices=devices_count.get(b.id, 0),
                accounts=accounts_n,
                goals=goals_count.get(b.id, 0),
                notes=notes_n,
                published_notes=published_count.get(b.id, 0),
                kb_documents=kb_count.get(b.id, 0),
                agent_runs=runs_count.get(b.id, 0),
                successful_runs=success_count.get(b.id, 0),
                notes_per_account=round(notes_n / accounts_n, 2) if accounts_n > 0 else 0.0,
            )
        )

    return BusinessComparisonResponse(items=items, total_businesses=len(items))
