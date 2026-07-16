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
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import filter_derived_by_business, get_db
from matrix.api.schemas import (
    NotificationItem,
    NotificationListResponse,
    NotificationMarkReadRequest,
    NotificationMarkReadResponse,
)
from matrix.db.models import Notification as NotificationORM
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


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
        business_id=n.business_id,  # v0.7+ 业务归属
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
    from matrix.db.models import Device, Goal, Note

    stmt = select(NotificationORM)
    count_stmt = select(func.count(NotificationORM.id))
    # v0.7+：衍生表业务过滤（4 个 FK 任一匹配业务即返回）
    sources = [
        (NotificationORM, Goal, "goal_id"),
        (NotificationORM, Note, "note_id"),
        (NotificationORM, Device, "device_id"),
        # run_id → agent_runs.business_id 在 helper 内不可达（agent_runs.business_id 已存在，
        # 但 chain 起来需要两次 EXISTS，复杂度高，暂不处理；run_id 直接走 agent_runs 表）
    ]
    stmt = filter_derived_by_business(stmt, business_id=business_id, sources=sources)
    count_stmt = filter_derived_by_business(
        count_stmt, business_id=business_id, sources=sources
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
    rows = (await session.execute(stmt)).scalars().all()
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    return NotificationListResponse(items=[_to_schema(r) for r in rows], total=total)


@router.post("/read", response_model=NotificationMarkReadResponse)
async def mark_read(
    body: NotificationMarkReadRequest,
    session: AsyncSession = Depends(get_db),
) -> NotificationMarkReadResponse:
    """标记已读。``ids=None`` 表示把所有未读一次性全部标记已读。

    幂等：已读项不会被重复覆盖（read_at 仍是原值）。
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
    result = await session.execute(stmt)
    await session.commit()
    marked = int(result.rowcount or 0)
    logger.info("notifications.mark_read", marked=marked, ids_provided=bool(body.ids))
    return NotificationMarkReadResponse(marked=marked)