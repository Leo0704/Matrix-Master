"""设备管理端点。

实现 devices.listDevices / registerDevice / getDevice / pairDevice。
``matrix.device`` 子系统目前仅有占位接口，故本路由直接对接 ORM + DB。
"""
from __future__ import annotations

import base64
import time
import uuid
from collections import deque
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db, resolve_active_business
from matrix.api.schemas import (
    Device,
    DeviceListResponse,
    DevicePairRequest,
    DevicePairResponse,
    DeviceRegisterRequest,
    DeviceRetireResponse,
    DeviceUpdate,
)
from matrix.db.models import Account, AppConfig, Business, Device as DeviceORM
from matrix.device.key_manager import KeyManager
from matrix.device.secret_box import encrypt_secret
from matrix.device.pair_codes import (
    claim_pair_code as _claim_pair_code,
    finalize_pair_code as _finalize_pair_code,
    issue_pair_code as _issue_pair_code,
    restore_pair_code as _restore_pair_code,
)
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])

# APK 首次配对时手里没有任何凭证（HMAC 密钥正是本端点下发的），因此 pair
# 端点必须活在控制台统一鉴权之外——单独一个 router，app.py 挂载时不加
# dependencies。它的安全保障 = 一次性配对码 + 上方失败限流。
pair_router = APIRouter(prefix="/devices", tags=["devices"])


# ---------------------------------------------------------------------------
# 配对失败限流（防在线爆破配对码）
# ---------------------------------------------------------------------------
# APK 流量经 socat 边车 / adb reverse 转发，request.client.host 恒为边车地址，
# per-IP 计数没有区分度——故用进程内全局滑窗。当前部署为单实例（scheduler 同样
# 假设单实例），进程内计数足够；窗口内失败超限直接 429，不区分"码错/码不存在"，
# 避免泄露码有效性。
_PAIR_FAIL_WINDOW_SEC = 300.0
_PAIR_FAIL_MAX = 10
_pair_failures: deque[float] = deque()


def _pair_rate_limited(now: float) -> bool:
    """惰性清理过期记录后，判断当前是否已超限。"""
    while _pair_failures and now - _pair_failures[0] > _PAIR_FAIL_WINDOW_SEC:
        _pair_failures.popleft()
    return len(_pair_failures) >= _PAIR_FAIL_MAX


def _to_schema(
    d: DeviceORM,
    bound_accounts: int = 0,
    bound_account_handle: str | None = None,
    pair_code: str | None = None,
    business_name: str | None = None,
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
        business_id=d.business_id,  # v0.7+ 业务归属
        business_name=business_name,
    )


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
    business_id: Optional[uuid.UUID] = Query(None, description="v0.7+ 业务过滤"),
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
    if business_id:
        stmt = stmt.where(DeviceORM.business_id == business_id)
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

    # 一次性拉业务名称，避免前端二次查询
    business_names: dict[uuid.UUID, str] = {}
    if rows:
        biz_ids = {r.business_id for r in rows if r.business_id}
        if biz_ids:
            biz_stmt = select(Business.id, Business.name).where(Business.id.in_(biz_ids))
            for bid, bname in (await session.execute(biz_stmt)).all():
                business_names[bid] = bname

    return DeviceListResponse(
        items=[
            _to_schema(
                r,
                counts.get(r.id, 0),
                handles.get(r.id),
                business_name=business_names.get(r.business_id),
            )
            for r in rows
        ]
    )


@router.post("/{device_id}/issue_pair", response_model=DevicePairResponse)
async def issue_pair_code_endpoint(
    device_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> DevicePairResponse:
    """P2-1 测试期：admin 触发生成配对码（写进程内 + 持久化文件）。

    为什么需要：之前配对码只能用 `docker exec python -c "..."` 生成，
    但 uvicorn 进程和 docker exec 进程是独立的 Python 进程，内存
    不共享。APK 通过 HTTP 调 uvicorn 时查不到配对码。这个 endpoint
    让 uvicorn 自己生成配对码并写到自己的内存 + 持久化文件。

    正式版应该删掉或挪到 admin 子系统。
    """
    d = await session.get(DeviceORM, device_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    code = _issue_pair_code(device_id)
    # 配对码本身不落日志（码=可领取 HMAC 密钥的凭证），只记设备维度
    logger.info("admin.issue_pair_code", device_id=str(device_id))
    return DevicePairResponse(
        device_id=device_id,
        key_id="admin-issued",  # 真 key_id 在 pair 时才签发；此处不嵌配对码
        hmac_key="",  # 不下发 secret，等 pair 时真发
        pair_code=code,
    )


@router.post("", response_model=Device, status_code=status.HTTP_201_CREATED)
async def register_device(
    body: DeviceRegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> Device:
    # v0.7+ 业务模型重构：业务上下文校验（存在 + active）
    await resolve_active_business(session, body.business_id)

    # P2-3：register 时只需 nickname（adb_serial 也可选）。其他 4 字段 APK 配对时回填。
    # 注意：以前的版本允许请求里只带 nickname，Pydantic 会给缺失的 Optional 字段喂 None；
    # 此处显式过滤非空字符串，避免空字符串被当占位入库后被 pair 覆盖逻辑误识别。
    d = DeviceORM(
        nickname=body.nickname,
        adb_serial=body.adb_serial,
        business_id=body.business_id,  # v0.7+ 业务归属
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
    biz_name = None
    if d.business_id:
        biz = await session.get(Business, d.business_id)
        biz_name = biz.name if biz else None
    return _to_schema(d, int(cnt), handle, business_name=biz_name)


@pair_router.post("/{device_id}/pair", response_model=DevicePairResponse)
async def pair_device(
    device_id: uuid.UUID,
    body: DevicePairRequest,
    session: AsyncSession = Depends(get_db),
) -> DevicePairResponse:
    """消费主控签发的一次性配对码并下发新的 HMAC 密钥。

    配对的真实 device_id **由配对码 entry 决定**，不取 URL path 里的
    ``device_id``——后者是 APK 本地生成的 UUID（见 ``DeviceRegistrar.deviceId``），
    与服务端 device 行的 UUID 不是同一个体系，历史上导致配对永远 404/400。
    path 形参仅为向后兼容保留，实际忽略；若与码绑定的 device_id 不一致会记
    一条 debug 日志方便排查。

    P2-3：可选接收 ``body.identity`` 块（model / android_version / apk_version / tailnet_ip），
    APK 上线时把它们写回 Device 行——替代了原本让用户在 register 时手填的字段。
    老 APK 只发 pair_code 也照样能配对，4 字段都缺也无副作用。
    """
    now = time.time()
    if _pair_rate_limited(now):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many failed pair attempts; try again later",
        )
    real_device_id = _claim_pair_code(body.pair_code)
    if real_device_id is None:
        _pair_failures.append(now)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "invalid, expired, or already used pair code"
        )
    if real_device_id != device_id:
        logger.debug(
            "pair.path_device_id_mismatch_ignored",
            path_device_id=str(device_id),
            code_bound_device_id=str(real_device_id),
        )

    d = await session.get(DeviceORM, real_device_id)
    if d is None or d.deleted_at is not None:
        # 领用了码但设备不存在：还原码，让用户排查后可重试同一码。
        _restore_pair_code(body.pair_code)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")

    try:
        key_manager = KeyManager(session)
        await key_manager.revoke_all(real_device_id)
        issued = await key_manager.issue_key(real_device_id)
        d.hmac_key_id = issued.key_id
        # 密钥原文信封加密（Fernet）后落 app_config，供 verify_hmac 验签时解密；
        # 明文只出现在此处内存与返回 APK 的一次性响应里。
        secret_key = f"hmac_secret:{issued.key_id}"
        secret_value = {"v": 1, "enc_secret": encrypt_secret(issued.secret)}
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
                    device_id=str(real_device_id),
                    fields=sorted(written.keys()),
                )

        await session.flush()
        # 显式 commit：把“DB 事务成功”纳入本函数的可观测区间，确保
        # 配对码的 finalize/restore 与 DB 持久化结果严格一致。
        await session.commit()
    except Exception:
        await session.rollback()
        # 事务失败：清掉领用标记，码可被同一用户重试，避免“码没了 key 也没下发”。
        _restore_pair_code(body.pair_code)
        raise

    # DB 已 durably commit。此处失败（文件 IO）不影响已下发的 key；
    # 残留的 consumed 标记会在 _CONSUMED_STALE_SECONDS 后自动复活。
    try:
        _finalize_pair_code(body.pair_code)
    except Exception as e:
        logger.warning(f"pair_codes finalize failed (code auto-revives later): {e}")

    return DevicePairResponse(
        device_id=real_device_id,
        key_id=issued.key_id,
        hmac_key=base64.b64encode(issued.secret).decode("ascii"),
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


@router.post("/{device_id}/retire", response_model=DeviceRetireResponse)
async def retire_device(
    device_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> DeviceRetireResponse:
    """退役设备：这台手机不再参与运营。

    业务语义：**设备 = 手机**。"退役"表示"设备坏了 / 不要了 / 永久下线"，
    会做三件事：
      - 把绑在这台设备上的账号 device_id 清 NULL（账号数据不丢）
      - 把设备 status 改成 'disabled'，从设备列表自动消失（list 默认排除）
      - 撤销该设备所有 HMAC 密钥，防止退役后还能冒充心跳/登录态上报

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

    # 撤销 HMAC 密钥：退役设备不应再能调用任何需 HMAC 鉴权的接口
    km = KeyManager(session)
    await km.revoke_all(device_id)
    d.hmac_key_id = None
    d.status = "disabled"
    await session.flush()
    return DeviceRetireResponse(
        device_id=device_id,
        unbound_account_handle=unbound_handle,
    )
