"""通知端点：列出和标记已读。

不同于 alerts（监控告警，resolved/unresolved 二态）：本表是终态用户通知，
read_at 表示已读，可选 typed FK 用于按维度过滤和跳详情。

GET    /notifications?unread=&code=&severity=&limit=&offset=
POST   /notifications/read                       标记已读（ids=None 表示全部未读）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from matrix.api.deps import get_db
from matrix.api.schemas import (
    NotificationDeleteResponse,
    NotificationItem,
    NotificationListResponse,
    NotificationMarkReadRequest,
    NotificationMarkReadResponse,
)
from matrix.db.models import Device, Goal, Note, Notification as NotificationORM
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _goal_name(goal: Goal | None) -> str | None:
    """从 Goal.target 里取主题作为显示名。"""
    if goal is None:
        return None
    target = goal.target or {}
    theme = target.get("theme")
    if isinstance(theme, str) and theme.strip():
        return theme.strip()
    return None


def _business_scope(business_id: uuid.UUID):
    """业务过滤条件（W5）：优先 notifications.business_id 新列；
    老数据（该列为 NULL）回退到 goal/note/device FK EXISTS 推导。"""
    fk_fallback = or_(
        and_(
            NotificationORM.goal_id.is_not(None),
            exists().where(
                Goal.id == NotificationORM.goal_id,
                Goal.business_id == business_id,
            ),
        ),
        and_(
            NotificationORM.note_id.is_not(None),
            exists().where(
                Note.id == NotificationORM.note_id,
                Note.business_id == business_id,
            ),
        ),
        and_(
            NotificationORM.device_id.is_not(None),
            exists().where(
                Device.id == NotificationORM.device_id,
                Device.business_id == business_id,
            ),
        ),
    )
    return or_(
        NotificationORM.business_id == business_id,
        and_(NotificationORM.business_id.is_(None), fk_fallback),
    )


def _to_schema(n: NotificationORM) -> NotificationItem:
    return NotificationItem(
        id=n.id,
        recipient=n.recipient,
        code=n.code,
        severity=n.severity,  # type: ignore[arg-type]
        title=n.title,
        body=n.body,
        goal_id=n.goal_id,
        run_id=n.run_id,
        note_id=n.note_id,
        device_id=n.device_id,
        payload=n.payload or {},
        read_at=n.read_at,
        created_at=n.created_at,
        business_id=n.business_id,  # v0.7+ 业务归属（019 migration 加列）
        # v0.7+ 消息可读化：关联实体名称
        goal_name=_goal_name(getattr(n, "goal", None)),
        note_title=getattr(getattr(n, "note", None), "title", None),
        device_name=getattr(getattr(n, "device", None), "nickname", None),
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    unread: Optional[bool] = Query(None),
    code: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    recipient: Optional[str] = Query(None),
    business_id: Optional[uuid.UUID] = Query(None, description="v0.7+ 业务过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> NotificationListResponse:
    stmt = select(NotificationORM)
    count_stmt = select(func.count(NotificationORM.id))
    # v0.7+ 业务过滤（W5）：优先 notifications.business_id 新列，
    # 老数据（NULL）回退 goal/note/device FK EXISTS 推导
    if business_id is not None:
        stmt = stmt.where(_business_scope(business_id))
        count_stmt = count_stmt.where(_business_scope(business_id))

    # 消息可读化：关联实体的名称一并带回去，减少前端二次查询
    stmt = stmt.options(
        joinedload(NotificationORM.goal),
        joinedload(NotificationORM.note),
        joinedload(NotificationORM.device),
    )

    if unread is True:
        stmt = stmt.where(NotificationORM.read_at.is_(None))
        count_stmt = count_stmt.where(NotificationORM.read_at.is_(None))
    elif unread is False:
        stmt = stmt.where(NotificationORM.read_at.is_not(None))
        count_stmt = count_stmt.where(NotificationORM.read_at.is_not(None))
    if code:
        stmt = stmt.where(NotificationORM.code == code)
        count_stmt = count_stmt.where(NotificationORM.code == code)
    if severity:
        stmt = stmt.where(NotificationORM.severity == severity)
        count_stmt = count_stmt.where(NotificationORM.severity == severity)
    if recipient:
        stmt = stmt.where(NotificationORM.recipient == recipient)
        count_stmt = count_stmt.where(NotificationORM.recipient == recipient)

    stmt = stmt.order_by(NotificationORM.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).unique().scalars().all()
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    return NotificationListResponse(items=[_to_schema(r) for r in rows], total=total)


@router.post("/digest", response_model=dict)
async def trigger_digest(
    session: AsyncSession = Depends(get_db),
) -> dict:
    """手动触发 AI 日报生成。"""
    from matrix.agent.daily_digest import DailyDigestGenerator
    from matrix.llm.router import get_default_client

    generator = DailyDigestGenerator(
        session_factory=lambda: session,
        llm_client=get_default_client(),
    )
    created = await generator.run_once()
    return {"created": created}


@router.post("/read", response_model=NotificationMarkReadResponse)
async def mark_read(
    body: NotificationMarkReadRequest,
    session: AsyncSession = Depends(get_db),
) -> NotificationMarkReadResponse:
    """标记已读。``ids=None`` 表示把所有未读一次性全部标记已读。

    幂等：已读项不会被重复覆盖（read_at 仍是原值）。
    传了 ``business_id`` 则只动本业务的通知（W5 业务隔离）。
    """
    now = datetime.now(timezone.utc)
    if body.ids:
        stmt = (
            update(NotificationORM)
            .where(
                NotificationORM.id.in_([uuid.UUID(str(i)) for i in body.ids]),
                NotificationORM.read_at.is_(None),
            )
            .values(read_at=now)
        )
    else:
        stmt = (
            update(NotificationORM)
            .where(NotificationORM.read_at.is_(None))
            .values(read_at=now)
        )
    if body.business_id is not None:
        stmt = stmt.where(_business_scope(body.business_id))
    result = await session.execute(stmt)
    await session.commit()
    marked = int(result.rowcount or 0)
    logger.info("notifications.mark_read", marked=marked, ids_provided=bool(body.ids))
    return NotificationMarkReadResponse(marked=marked)


@router.delete("/{notification_id}", response_model=NotificationDeleteResponse)
async def delete_notification(
    notification_id: uuid.UUID,
    business_id: Optional[uuid.UUID] = Query(
        None, description="v0.7+ 业务约束：传了就只删本业务的通知"
    ),
    session: AsyncSession = Depends(get_db),
) -> NotificationDeleteResponse:
    """删除单条通知。传了 ``business_id`` 则只动本业务的（W5 业务隔离）。"""
    stmt = delete(NotificationORM).where(NotificationORM.id == notification_id)
    if business_id is not None:
        stmt = stmt.where(_business_scope(business_id))
    result = await session.execute(stmt)
    await session.commit()
    deleted = int(result.rowcount or 0)
    logger.info("notifications.delete", notification_id=str(notification_id), deleted=deleted)
    return NotificationDeleteResponse(deleted=deleted)


@router.post("/clear-read", response_model=NotificationDeleteResponse)
async def clear_read_notifications(
    business_id: Optional[uuid.UUID] = Query(
        None, description="v0.7+ 业务约束：传了就只清本业务的已读通知"
    ),
    session: AsyncSession = Depends(get_db),
) -> NotificationDeleteResponse:
    """一键清空所有已读通知。传了 ``business_id`` 则只动本业务的（W5 业务隔离）。"""
    stmt = delete(NotificationORM).where(NotificationORM.read_at.is_not(None))
    if business_id is not None:
        stmt = stmt.where(_business_scope(business_id))
    result = await session.execute(stmt)
    await session.commit()
    deleted = int(result.rowcount or 0)
    logger.info("notifications.clear_read", deleted=deleted)
    return NotificationDeleteResponse(deleted=deleted)
