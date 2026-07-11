"""主控设置端点（key-value 持久化，存 app_config 表）。

简单 GET/PUT 全部设置；前端按 key 读写。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import AppSetting, AppSettingList, AppSettingUpsert
from matrix.db.models import AppConfig
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])
_INTERNAL_KEY_PREFIXES = ("hmac_secret:",)


def _is_internal_key(key: str) -> bool:
    return key.startswith(_INTERNAL_KEY_PREFIXES)


def _to_schema(row: AppConfig) -> AppSetting:
    return AppSetting(
        key=row.key,
        value=dict(row.value or {}),
        description=row.description,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.get("", response_model=AppSettingList)
async def list_settings(
    session: AsyncSession = Depends(get_db),
) -> AppSettingList:
    rows = (await session.execute(select(AppConfig).order_by(AppConfig.key))).scalars().all()
    rows = [row for row in rows if not _is_internal_key(row.key)]
    return AppSettingList(items=[_to_schema(r) for r in rows])


@router.get("/{key}", response_model=AppSetting)
async def get_setting(
    key: str, session: AsyncSession = Depends(get_db)
) -> AppSetting:
    if _is_internal_key(key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "setting not found")
    row = await session.get(AppConfig, key)
    if row is None:
        # 不存在时返回空值，前端可按需处理（避免 404 让前端逻辑复杂）
        return AppSetting(key=key, value={}, description=None)
    return _to_schema(row)


@router.put("/{key}", response_model=AppSetting)
async def upsert_setting(
    key: str,
    body: AppSettingUpsert,
    session: AsyncSession = Depends(get_db),
) -> AppSetting:
    if _is_internal_key(key):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "internal settings cannot be modified")
    row = await session.get(AppConfig, key)
    now = datetime.now(timezone.utc)
    if row is None:
        row = AppConfig(
            key=key,
            value=body.value,
            description=body.description,
            updated_at=now,
        )
        session.add(row)
    else:
        row.value = body.value
        if body.description is not None:
            row.description = body.description
        row.updated_at = now
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "settings upsert failed")
    logger.info("settings.upsert", key=key)
    return _to_schema(row)
