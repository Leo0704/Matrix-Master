"""笔记 CRUD 端点。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    Note,
    NoteCreate,
    NoteListResponse,
    NoteUpdate,
)
from matrix.db.models import Note as NoteORM
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/notes", tags=["notes"])


def _to_schema(n: NoteORM) -> Note:
    return Note(
        id=n.id,
        account_id=n.account_id,
        title=n.title,
        content=n.content,
        images=list(n.images or []),
        tags=list(n.tags or []),
        status=n.status,  # type: ignore[arg-type]
        platform_note_id=n.platform_note_id,
        platform_url=n.platform_url,
        scheduled_at=n.scheduled_at,
        published_at=n.published_at,
    )


@router.get("", response_model=NoteListResponse)
async def list_notes(
    account_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> NoteListResponse:
    base = select(NoteORM).where(NoteORM.deleted_at.is_(None))
    if account_id:
        base = base.where(NoteORM.account_id == account_id)
    if status_filter:
        base = base.where(NoteORM.status == status_filter)

    total = (
        await session.execute(select(func.count()).select_from(base.subquery()))
    ).scalar_one()

    stmt = base.order_by(NoteORM.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    return NoteListResponse(items=[_to_schema(r) for r in rows], total=int(total))


@router.get("/{note_id}", response_model=Note)
async def get_note(
    note_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Note:
    n = await session.get(NoteORM, note_id)
    if n is None or n.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    return _to_schema(n)


@router.post("", response_model=Note, status_code=status.HTTP_201_CREATED)
async def create_note(
    body: NoteCreate, session: AsyncSession = Depends(get_db)
) -> Note:
    from matrix.db.models import Account

    acct = await session.get(Account, body.account_id)
    if acct is None or acct.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "account not found")
    n = NoteORM(
        account_id=body.account_id,
        title=body.title,
        content=body.content,
        images=body.images,
        tags=body.tags,
        status=body.status,
        scheduled_at=body.scheduled_at,
    )
    session.add(n)
    await session.flush()
    return _to_schema(n)


@router.patch("/{note_id}", response_model=Note)
async def update_note(
    note_id: uuid.UUID,
    body: NoteUpdate,
    session: AsyncSession = Depends(get_db),
) -> Note:
    n = await session.get(NoteORM, note_id)
    if n is None or n.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")

    data = body.model_dump(exclude_unset=True)
    for key, val in data.items():
        setattr(n, key, val)
    n.updated_at = datetime.now(timezone.utc)
    if "status" in data and data["status"] == "published" and n.published_at is None:
        n.published_at = n.updated_at
    await session.flush()
    return _to_schema(n)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(
    note_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> None:
    n = await session.get(NoteORM, note_id)
    if n is None or n.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "note not found")
    n.deleted_at = datetime.now(timezone.utc)
    n.updated_at = n.deleted_at
    n.status = "deleted"
    await session.flush()
    logger.info("note.soft_delete id=%s", note_id)
