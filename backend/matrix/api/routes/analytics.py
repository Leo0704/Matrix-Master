"""分析 / 图表端点：账号内容表现聚合（数据看板用）。

- GET /analytics/account-content-stats   每个账号的笔记数 + 已发布 + 草稿 + 平均曝光点赞评论
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    AccountContentStats,
    AccountContentStatsResponse,
)
from matrix.db.models import Account, Device, Note, NoteMetric
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
