"""设备管理端点。

实现 devices.listDevices / registerDevice / getDevice / pairDevice。
``matrix.device`` 子系统目前仅有占位接口，故本路由直接对接 ORM + DB。
"""
from __future__ import annotations

import base64
import secrets
import time
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
    DeviceUnbindResponse,
    DeviceUpdate,
)
from matrix.db.models import Account, AppConfig, Device as DeviceORM
from matrix.device.key_manager import KeyManager
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])
_PAIR_CODE_TTL_SECONDS = 600
_pair_codes: dict[str, tuple[uuid.UUID, float]] = {}


def _to_schema(
    d: DeviceORM,
    bound_accounts: int = 0,
    bound_account_handle: str | None = None,
    pair_code: str | None = None,
) -> Device:
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
        bound_account_handle=bound_account_handle,
        pair_code=pair_code,
    )


def _issue_pair_code(device_id: uuid.UUID) -> str:
    now = time.monotonic()
    for code, (_, expires_at) in list(_pair_codes.items()):
        if expires_at <= now:
            _pair_codes.pop(code, None)
    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code not in _pair_codes:
            _pair_codes[code] = (device_id, now + _PAIR_CODE_TTL_SECONDS)
            return code


def _consume_pair_code(device_id: uuid.UUID, pair_code: str) -> bool:
    entry = _pair_codes.get(pair_code)
    if entry is None:
        return False
    expected_device_id, expires_at = entry
    if expires_at <= time.monotonic():
        _pair_codes.pop(pair_code, None)
        return False
    if expected_device_id != device_id:
        return False
    _pair_codes.pop(pair_code, None)
    return True


def _apply_pair_identity(
    d: DeviceORM, identity: "DevicePairIdentity"
) -> dict[str, str]:
    """把 APK 配对时上报的 4 字段写回 Device 行。

    只写非空字段（含空字符串视为占位跳过，避免 APK 端串空值过来把已经填好的字段清掉）。
    返回实际写入的字段名 → 值的 dict，供调用方记日志。
    """
    candidates: dict[str, str | None] = {
        "model": identity.model,
        "android_version": identity.android_version,
        "apk_version": identity.apk_version,
        "tailnet_ip": identity.tailnet_ip,
    }
    written: dict[str, str] = {}
    for column, value in candidates.items():
        if isinstance(value, str) and value != "":
            setattr(d, column, value)
            written[column] = value
    return written


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    status_filter: Optional[str] = Query(None, alias="status"),
    tag: Optional[str] = Query(None),
    include_disabled: bool = Query(False, description="默认排除已退役设备（status=disabled）"),
    session: AsyncSession = Depends(get_db),
) -> DeviceListResponse:
    stmt = select(DeviceORM).where(DeviceORM.deleted_at.is_(None))
    if not include_disabled:
        # 默认排除 status='disabled'（"解绑"=设备退役后自动从列表消失）
        stmt = stmt.where(DeviceORM.status != "disabled")
    if status_filter:
        stmt = stmt.where(DeviceORM.status == status_filter)
    if tag:
        stmt = stmt.where(DeviceORM.tags.any(tag))
    stmt = stmt.order_by(DeviceORM.created_at.desc())

    rows = (await session.execute(stmt)).scalars().all()
    # bound_accounts 一次性 count + handle（严格 1 机 1 账号下最多一个）
    counts: dict[uuid.UUID, int] = {}
    handles: dict[uuid.UUID, str] = {}
    if rows:
        ids = [r.id for r in rows]
        bind_stmt = select(Account.device_id, Account.handle).where(
            Account.device_id.in_(ids), Account.deleted_at.is_(None)
        )
        for did, h in (await session.execute(bind_stmt)).all():
            counts[did] = counts.get(did, 0) + 1
            # 1:1 下至多覆盖一次；若多个则取第一个（应被 migration unique 阻止）
            handles.setdefault(did, h)

    return DeviceListResponse(
        items=[
            _to_schema(r, counts.get(r.id, 0), handles.get(r.id))
            for r in rows
        ]
    )


@router.post("", response_model=Device, status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceRegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> Device:
    # P2-3：register 时只需 nickname（adb_serial 也可选）。其他 4 字段 APK 配对时回填。
    # 注意：以前的版本允许请求里只带 nickname，Pydantic 会给缺失的 Optional 字段喂 None；
    # 此处显式过滤非空字符串，避免空字符串被当占位入库后被 pair 覆盖逻辑误识别。
    d = DeviceORM(
        nickname=body.nickname,
        adb_serial=body.adb_serial,
        tags=[],
        status="pending",
    )
    session.add(d)
    await session.flush()
    return _to_schema(d, pair_code=_issue_pair_code(d.id))


@router.get("/{device_id}", response_model=Device)
async def get_device(
    device_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Device:
    d = await session.get(DeviceORM, device_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    bind_row = (
        await session.execute(
            select(Account.handle).where(
                Account.device_id == device_id,
                Account.deleted_at.is_(None),
            )
        )
    ).first()
    handle = bind_row[0] if bind_row else None
    cnt = (
        await session.execute(
            select(func.count(Account.id)).where(
                Account.device_id == device_id,
                Account.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    return _to_schema(d, int(cnt), handle)


@router.post("/{device_id}/pair", response_model=DevicePairResponse)
async def pair_device(
    device_id: uuid.UUID,
    body: DevicePairRequest,
    session: AsyncSession = Depends(get_db),
) -> DevicePairResponse:
    """消费主控签发的一次性配对码并下发新的 HMAC 密钥。

    P2-3：可选接收 ``body.identity`` 块（model / android_version / apk_version / tailnet_ip），
    APK 上线时把它们写回 Device 行——替代了原本让用户在 register 时手填的字段。
    老 APK 只发 pair_code 也照样能配对，4 字段都缺也无副作用。
    """
    d = await session.get(DeviceORM, device_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")

    if not _consume_pair_code(device_id, body.pair_code):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "invalid, expired, or already used pair code"
        )

    key_manager = KeyManager(session)
    await key_manager.revoke_all(device_id)
    issued = await key_manager.issue_key(device_id)
    d.hmac_key_id = issued.key_id
    secret_key = f"hmac_secret:{issued.key_id}"
    secret_value = {"secret": base64.b64encode(issued.secret).decode("ascii")}
    secret_row = await session.get(AppConfig, secret_key)
    if secret_row is None:
        session.add(
            AppConfig(
                key=secret_key,
                value=secret_value,
                description="Internal device HMAC secret; never expose through settings API.",
            )
        )
    else:
        secret_row.value = secret_value
    if d.status == "pending":
        d.status = "active"

    # P2-3：写回 APK 自报的 4 字段（仅当 body.identity 非 None）
    if body.identity is not None:
        written = _apply_pair_identity(d, body.identity)
        if written:
            logger.info(
                "pair_identity.applied",
                device_id=str(device_id),
                fields=sorted(written.keys()),
            )

    await session.flush()
    return DevicePairResponse(
        key_id=issued.key_id,
        hmac_key=secret_value["secret"],
    )


@router.patch("/{device_id}", response_model=Device)
async def update_device(
    device_id: uuid.UUID,
    body: DeviceUpdate,
    session: AsyncSession = Depends(get_db),
) -> Device:
    """改设备 nickname / tags（局部更新）。"""
    d = await session.get(DeviceORM, device_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    if body.nickname is not None:
        d.nickname = body.nickname
    if body.tags is not None:
        d.tags = body.tags
    await session.flush()
    # 重新拿一次返回带 handles
    bind_row = (
        await session.execute(
            select(Account.handle).where(
                Account.device_id == device_id,
                Account.deleted_at.is_(None),
            )
        )
    ).first()
    handle = bind_row[0] if bind_row else None
    cnt = (
        await session.execute(
            select(func.count(Account.id)).where(
                Account.device_id == device_id,
                Account.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    return _to_schema(d, int(cnt), handle)


@router.post("/{device_id}/unbind", response_model=DeviceUnbindResponse)
async def unbind_device(
    device_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> DeviceUnbindResponse:
    """设备退役：清空绑到这台设备上的 active 账号的 device_id + 标 disabled。

    业务语义：**设备 = 手机**。"解绑"实际上就是"设备坏了 / 不要了"，
    所以一次性做两件事：
      - 把绑在这台设备上的账号 device_id 清 NULL（账号数据不丢）
      - 把设备 status 改成 'disabled'，从设备列表自动消失（list 默认排除）

    注意：notes 仍挂在账号下，账号历史完整；只是这台手机不再参与运营。
    """
    d = await session.get(DeviceORM, device_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")

    bind_rows = (
        await session.execute(
            select(Account).where(
                Account.device_id == device_id,
                Account.deleted_at.is_(None),
            )
        )
    ).scalars().all()

    unbound_handle: str | None = None
    for acc in bind_rows:
        if unbound_handle is None:
            unbound_handle = acc.handle
        acc.device_id = None
    d.status = "disabled"
    await session.flush()
    return DeviceUnbindResponse(
        device_id=device_id,
        unbound_account_handle=unbound_handle,
    )
