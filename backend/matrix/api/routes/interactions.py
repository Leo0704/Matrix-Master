"""互动（v0.6）HTTP 端点 — 只读。

写入由 ``interact_node`` 节点完成，运营者不动。
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import filter_derived_by_business, get_db
from matrix.api.schemas import Interaction, InteractionListResponse
from matrix.db.models import Interaction as InteractionORM

router = APIRouter(prefix="/interactions", tags=["interactions"])


def _to_schema(i: InteractionORM) -> Interaction:
    return Interaction(
        id=i.id,
        account_id=i.account_id,
        target_note_id=i.target_note_id,
        target_user=i.target_user,
        type=i.type,  # type: ignore[arg-type]
        content=i.content,
        ts=i.ts,
        result=i.result,  # type: ignore[arg-type]
        error_message=i.error_message,
        request_id=i.request_id,
    )


@router.get("", response_model=InteractionListResponse)
async def list_interactions(
    account_id: Optional[uuid.UUID] = Query(None, description="按账号过滤"),
    target_note_id: Optional[uuid.UUID] = Query(None, description="按目标笔记过滤"),
    type: Optional[str] = Query(None, description="按互动类型过滤 (like/comment/...)"),
    result: Optional[str] = Query(None, description="按结果过滤 (success/failed/pending)"),
    business_id: Optional[uuid.UUID] = Query(None, description="v0.7+ 业务过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> InteractionListResponse:
    """分页列互动记录（默认按时间倒序）。"""
    from matrix.db.models import Account, Note

    stmt = select(InteractionORM).order_by(InteractionORM.ts.desc())
    count_stmt = select(func.count(InteractionORM.id))
    # v0.7+：衍生表业务过滤（通过 Account 或 Note 的 business_id）
    sources = [
        (InteractionORM, Account, "account_id"),
        (InteractionORM, Note, "target_note_id"),
    ]
    stmt = filter_derived_by_business(stmt, business_id=business_id, sources=sources)
    count_stmt = filter_derived_by_business(
        count_stmt, business_id=business_id, sources=sources
    )
    if account_id is not None:
        stmt = stmt.where(InteractionORM.account_id == account_id)
        count_stmt = count_stmt.where(InteractionORM.account_id == account_id)
    if target_note_id is not None:
        stmt = stmt.where(InteractionORM.target_note_id == target_note_id)
        count_stmt = count_stmt.where(InteractionORM.target_note_id == target_note_id)
    if type is not None:
        stmt = stmt.where(InteractionORM.type == type)
        count_stmt = count_stmt.where(InteractionORM.type == type)
    if result is not None:
        stmt = stmt.where(InteractionORM.result == result)
        count_stmt = count_stmt.where(InteractionORM.result == result)

    stmt = stmt.limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    return InteractionListResponse(items=[_to_schema(r) for r in rows], total=total)


@router.get("/{interaction_id}", response_model=Interaction)
async def get_interaction(
    interaction_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> Interaction:
    stmt = select(InteractionORM).where(InteractionORM.id == interaction_id)
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "interaction not found")
    return _to_schema(row)


__all__ = ["router"]
