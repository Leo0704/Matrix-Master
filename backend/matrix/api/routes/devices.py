"""设备管理端点。

实现 devices.listDevices / registerDevice / getDevice / pairDevice。
``matrix.device`` 子系统目前仅有占位接口，故本路由直接对接 ORM + DB。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
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
    DeviceUnbindResponse,
    DeviceUpdate,
)
from matrix.db.models import Account, AppConfig, Device as DeviceORM
from matrix.device.key_manager import KeyManager
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])
_PAIR_CODE_TTL_SECONDS = 600

# P2-1（real-device testing 改）：原来 _pair_codes 是进程内 dict，
# uvicorn 多 worker / 重启 / debug 时会丢；docker exec 跑的临时 Python
# 进程甚至根本没共享内存。改成 JSON 文件持久化：每次写都原子重命名。
#
# 关键：所有时间都用 wall-clock (time.time()) 而不是 monotonic，因为
# monotonic 是"进程启动后秒数"，跨进程无意义；wall-clock 是绝对时间，
# 跨进程可比较。
_PAIR_CODES_PATH = Path(
    os.environ.get("MATRIX_PAIR_CODES_PATH", "/app/backend/.pair_codes.json")
)


def _load_pair_codes() -> dict[str, tuple[str, float, Optional[float]]]:
    """Load from disk.

    Returns mapping code → (device_id_str, expires_at_wall, consumed_at_wall|None)。
    兼容旧版 2-tuple 条目（consumed_at 视作 None）。
    """
    if not _PAIR_CODES_PATH.exists():
        return {}
    try:
        raw = json.loads(_PAIR_CODES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, tuple[str, float, Optional[float]]] = {}
    for code, v in raw.items():
        if isinstance(v, list) and len(v) >= 3:
            cons = v[2]
            out[code] = (v[0], float(v[1]), float(cons) if cons is not None else None)
        else:
            # legacy: [device_id_str, expires_at]
            out[code] = (v[0], float(v[1]), None)
    return out


def _save_pair_codes(
    codes: dict[str, tuple[uuid.UUID, float, Optional[float]]],
) -> None:
    """Atomic write: write to temp file, then rename."""
    serializable = {
        code: (
            str(device_id),
            float(expires_at_wall),
            float(consumed_at) if consumed_at is not None else None,
        )
        for code, (device_id, expires_at_wall, consumed_at) in codes.items()
    }
    try:
        _PAIR_CODES_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".pair_codes.", dir=str(_PAIR_CODES_PATH.parent)
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(serializable, f)
            os.replace(tmp, _PAIR_CODES_PATH)
        except Exception:
            try: os.unlink(tmp)
            except OSError: pass
            raise
    except OSError as e:
        logger.warning(f"pair_codes persist failed: {e}")


def _purge_expired(
    codes: dict[str, tuple[uuid.UUID, float, Optional[float]]],
) -> dict[str, tuple[uuid.UUID, float, Optional[float]]]:
    now = time.time()
    return {c: v for c, v in codes.items() if v[1] > now}


# In-process cache; loaded on first access, persisted on every mutation.
_pair_codes_cache: Optional[dict[str, tuple[uuid.UUID, float, Optional[float]]]] = None


def _codes() -> dict[str, tuple[uuid.UUID, float, Optional[float]]]:
    global _pair_codes_cache
    if _pair_codes_cache is None:
        loaded = _load_pair_codes()
        _pair_codes_cache = {
            c: (uuid.UUID(d), e, cons) for c, (d, e, cons) in loaded.items()
        }
    return _pair_codes_cache


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
        business_id=d.business_id,  # v0.7+ 业务归属
    )


def _issue_pair_code(device_id: uuid.UUID) -> str:
    codes = _purge_expired(_codes())
    expires_at_wall = time.time() + _PAIR_CODE_TTL_SECONDS
    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code not in codes:
            codes[code] = (device_id, expires_at_wall, None)
            _save_pair_codes(codes)
            return code


def _claim_pair_code(pair_code: str) -> Optional[uuid.UUID]:
    """原子地领用配对码，返回其绑定的真实 device_id（**不删**条目）。

    配对码在 ``_issue_pair_code`` 时已绑定服务端 device 行的 UUID；
    本函数返回那个 UUID，**不**接受外部 device_id 做匹配。

    历史 bug：旧版 ``_consume_pair_code(device_id, pair_code)`` 要求 URL path
    里的 device_id 与码绑定的 device_id 一致，但 APK 在
    ``DeviceRegistrar.deviceId()`` 里本地随机生成 device_id、从不与服务端
    同步，导致两者永远不等 → 配对永远 404/400。改成由码反查 device_id
    彻底消除这个不一致。

    原子性策略（修复"码消费了但 key 没下发"的不可恢复态）：
    - 领用时只标记 ``consumed_at``，**不删**条目；并发请求看到
      ``consumed_at`` 在 stale 窗口内 → 直接拒绝（一次性防重放）。
    - DB 事务 commit 成功后由调用方调 :func:`_finalize_pair_code` 删条目。
    - 任何异常 / 回滚由调用方调 :func:`_restore_pair_code` 清掉
      ``consumed_at``，码可重试。
    - 进程崩溃留下的"领用未 finalize"条目，超过
      ``_CONSUMED_STALE_SECONDS`` 后自动可重新领用。

    返回：码不存在 / 已领用(未 stale) / 过期 → None；码有效 → device_id。
    """
    codes = _purge_expired(_codes())
    entry = codes.get(pair_code)
    if entry is None:
        return None
    expected_device_id, expires_at_wall, consumed_at = entry
    now = time.time()
    if expires_at_wall <= now:
        codes.pop(pair_code, None)
        _save_pair_codes(codes)
        return None
    if consumed_at is not None and (now - consumed_at) < _CONSUMED_STALE_SECONDS:
        return None
    # 原子领用：标记 consumed_at 并立即落盘。真正的删除交给
    # _finalize_pair_code（DB commit 成功后）。
    codes[pair_code] = (expected_device_id, expires_at_wall, now)
    _save_pair_codes(codes)
    return expected_device_id


# 领用后若超过此窗口仍未 finalize（进程崩溃 / commit 失败未还原），
# 下次访问时视作可重新领用，避免码被永久卡死。
_CONSUMED_STALE_SECONDS = 60


def _finalize_pair_code(pair_code: str) -> None:
    """DB 事务成功 commit 后调用：真正删除配对码条目。"""
    codes = _codes()
    if pair_code in codes:
        codes.pop(pair_code, None)
        _save_pair_codes(codes)


def _restore_pair_code(pair_code: str) -> None:
    """领用后事务失败时调用：清掉 consumed_at，让码可重新领用。"""
    codes = _codes()
    entry = codes.get(pair_code)
    if entry is None:
        return
    device_id, expires_at_wall, _ = entry
    codes[pair_code] = (device_id, expires_at_wall, None)
    _save_pair_codes(codes)


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

    return DeviceListResponse(
        items=[
            _to_schema(r, counts.get(r.id, 0), handles.get(r.id))
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
    logger.info("admin.issue_pair_code", device_id=str(device_id), code=code)
    return DevicePairResponse(
        key_id=f"admin-issued-{code}",
        hmac_key="",  # 不下发 secret，等 pair 时真发
        pair_code=code,
    )


@router.get("/{device_id}/_debug_pair_codes", response_model=dict)
async def debug_pair_codes(device_id: uuid.UUID) -> dict:
    """P2-1 测试期：返回 uvicorn 进程内 _pair_codes_cache 的内容 + 文件内容。

    用 docker exec 看不到 uvicorn 进程内变量，需要这个 endpoint。
    """
    cache = _codes()  # ensure loaded
    purged = _purge_expired(cache)
    return {
        "device_id": str(device_id),
        "cache_size": len(cache),
        "cache_after_purge_size": len(purged),
        "cache_keys": list(cache.keys()),
        "file_exists": _PAIR_CODES_PATH.exists(),
        "file_content": _PAIR_CODES_PATH.read_text() if _PAIR_CODES_PATH.exists() else None,
        "now_wall": time.time(),
    }


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
    return _to_schema(d, int(cnt), handle)


@router.post("/{device_id}/pair", response_model=DevicePairResponse)
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
    real_device_id = _claim_pair_code(body.pair_code)
    if real_device_id is None:
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
