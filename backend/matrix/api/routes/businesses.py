"""业务 CRUD 端点（v0.7+ 业务模型重构）。

业务是项目根，所有核心资源挂在业务名下。
业务支持软归档（status='archived'，不删行）；状态变更走独立 /archive /unarchive 端点。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    Business,
    BusinessCreate,
    BusinessListResponse,
    BusinessUpdate,
)
from matrix.db.models import Business as BusinessORM

router = APIRouter(prefix="/businesses", tags=["businesses"])


def _to_schema(b: BusinessORM) -> Business:
    return Business(
        id=b.id,
        name=b.name,
        slug=b.slug,
        description=b.description,
        status=b.status,  # type: ignore[arg-type]
        created_at=b.created_at,
        updated_at=b.updated_at,
        archived_at=b.archived_at,
    )


@router.get("", response_model=BusinessListResponse)
async def list_businesses(
    status_filter: Optional[str] = Query(
        None, alias="status", description="active | archived（不传返全部）"
    ),
    session: AsyncSession = Depends(get_db),
) -> BusinessListResponse:
    """列业务（含 archived）。"""
    stmt = select(BusinessORM)
    if status_filter:
        stmt = stmt.where(BusinessORM.status == status_filter)
    stmt = stmt.order_by(BusinessORM.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return BusinessListResponse(
        items=[_to_schema(r) for r in rows], total=len(rows)
    )


@router.post("", response_model=Business, status_code=status.HTTP_201_CREATED)
async def create_business(
    body: BusinessCreate,
    session: AsyncSession = Depends(get_db),
) -> Business:
    """建业务。slug 全局 UNIQUE。"""
    # slug 唯一性预检
    exists = (
        await session.execute(
            select(BusinessORM).where(BusinessORM.slug == body.slug)
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"business slug '{body.slug}' already exists",
        )

    b = BusinessORM(
        name=body.name,
        slug=body.slug,
        description=body.description,
        status="active",
    )
    session.add(b)
    await session.flush()
    return _to_schema(b)


@router.get("/{business_id}", response_model=Business)
async def get_business(
    business_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Business:
    b = await session.get(BusinessORM, business_id)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business not found")
    return _to_schema(b)


@router.patch("/{business_id}", response_model=Business)
async def update_business(
    business_id: uuid.UUID,
    body: BusinessUpdate,
    session: AsyncSession = Depends(get_db),
) -> Business:
    """改业务属性（局部更新）。

    status 不暴露（走 /archive /unarchive 端点，语义清晰）。
    """
    b = await session.get(BusinessORM, business_id)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business not found")
    if body.name is not None:
        b.name = body.name
    if body.slug is not None and body.slug != b.slug:
        # slug 唯一性预检
        exists = (
            await session.execute(
                select(BusinessORM).where(BusinessORM.slug == body.slug)
            )
        ).scalar_one_or_none()
        if exists is not None and exists.id != b.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"business slug '{body.slug}' already exists",
            )
        b.slug = body.slug
    if body.description is not None:
        b.description = body.description
    b.updated_at = datetime.utcnow()
    await session.flush()
    return _to_schema(b)


@router.post("/{business_id}/archive", response_model=Business)
async def archive_business(
    business_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Business:
    """软归档：status=archived + archived_at=now。

    archived 业务下不能再创建资源（POST 返回 409），但历史数据保留只读可查。
    """
    b = await session.get(BusinessORM, business_id)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business not found")
    if b.status == "archived":
        return _to_schema(b)  # 幂等
    b.status = "archived"
    b.archived_at = datetime.utcnow()
    b.updated_at = b.archived_at
    await session.flush()
    return _to_schema(b)


@router.post("/{business_id}/unarchive", response_model=Business)
async def unarchive_business(
    business_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Business:
    """恢复：status=active + archived_at=NULL。"""
    b = await session.get(BusinessORM, business_id)
    if b is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "business not found")
    if b.status == "active":
        return _to_schema(b)  # 幂等
    b.status = "active"
    b.archived_at = None
    b.updated_at = datetime.utcnow()
    await session.flush()
    return _to_schema(b)