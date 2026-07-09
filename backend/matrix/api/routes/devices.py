"""设备管理端点。

实现 devices.listDevices / registerDevice / getDevice / pairDevice。
``matrix.device`` 子系统目前仅有占位接口，故本路由直接对接 ORM + DB。
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    Device,
    DeviceListResponse,
    DevicePairRequest,
    DevicePairResponse,
    DeviceRegisterRequest,
)
from matrix.db.models import Account, Device as DeviceORM, DeviceHmacKey
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


def _to_schema(d: DeviceORM, bound_accounts: int = 0) -> Device:
    return Device(
        id=d.id,
        nickname=d.nickname,
        model=d.model,
        android_version=d.android_version,
        apk_version=d.apk_version,
        tailnet_ip=str(d.tailnet_ip) if d.tailnet_ip else None,
        tags=list(d.tags or []),
        status=d.status,  # type: ignore[arg-type]
        last_heartbeat=d.last_heartbeat,
        bound_accounts=bound_accounts,
    )


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    status_filter: Optional[str] = Query(None, alias="status"),
    tag: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_db),
) -> DeviceListResponse:
    stmt = select(DeviceORM).where(DeviceORM.deleted_at.is_(None))
    if status_filter:
        stmt = stmt.where(DeviceORM.status == status_filter)
    if tag:
        stmt = stmt.where(DeviceORM.tags.any(tag))
    stmt = stmt.order_by(DeviceORM.created_at.desc())

    rows = (await session.execute(stmt)).scalars().all()
    # bound_accounts 一次性 count
    counts: dict[uuid.UUID, int] = {}
    if rows:
        ids = [r.id for r in rows]
        cnt_stmt = (
            select(Account.device_id, func.count(Account.id))
            .where(Account.device_id.in_(ids), Account.deleted_at.is_(None))
            .group_by(Account.device_id)
        )
        for did, c in (await session.execute(cnt_stmt)).all():
            counts[did] = int(c)

    return DeviceListResponse(items=[_to_schema(r, counts.get(r.id, 0)) for r in rows])


@router.post("", response_model=Device, status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceRegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> Device:
    d = DeviceORM(
        nickname=body.nickname,
        model=body.model,
        android_version=body.android_version,
        apk_version=body.apk_version,
        tailnet_ip=body.tailnet_ip,
        adb_serial=body.adb_serial,
        tags=[],
        status="pending",
    )
    session.add(d)
    await session.flush()
    return _to_schema(d)


@router.get("/{device_id}", response_model=Device)
async def get_device(
    device_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Device:
    d = await session.get(DeviceORM, device_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    cnt = (
        await session.execute(
            select(func.count(Account.id)).where(
                Account.device_id == device_id,
                Account.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    return _to_schema(d, int(cnt))


@router.post("/{device_id}/pair", response_model=DevicePairResponse)
async def pair_device(
    device_id: uuid.UUID,
    body: DevicePairRequest,
    session: AsyncSession = Depends(get_db),
) -> DevicePairResponse:
    """配对：验证配对码后下发一次性 HMAC 密钥。

    实现：
    - pair_code 在生产环境应当通过短信 / 屏幕显示下发并与 device.adb_serial 绑定；
      本端做长度校验即可。
    - 生成 32 字节随机密钥，base64 返回给 APK；DB 只存 hash（key_hash）。
    """
    d = await session.get(DeviceORM, device_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")

    if not body.pair_code.isdigit() or len(body.pair_code) != 6:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "pair_code must be 6 digits",
        )

    raw_key = secrets.token_bytes(32)
    key_b64 = base64.b64encode(raw_key).decode("ascii")
    key_hash = hashlib.sha256(raw_key).digest()

    hk = DeviceHmacKey(
        id=body.hmac_key_id,
        device_id=device_id,
        key_hash=key_hash,
    )
    session.add(hk)
    d.hmac_key_id = body.hmac_key_id
    if d.status == "pending":
        d.status = "active"
    await session.flush()
    return DevicePairResponse(hmac_key=key_b64)
