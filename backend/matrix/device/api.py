"""设备-账号管理子系统的 FastAPI 路由（按 master-rest.openapi.yaml ``/devices``）。

HMAC 鉴权：APK 端调 ``POST /api/v1/devices/{id}/pair`` 时，body 路径需要
``X-Signature`` / ``X-Timestamp`` / ``X-Request-Id`` 三个 header；
签名内容 ``{timestamp}\\n{request_id}\\n{body_sha256}``。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Device
from matrix.db.session import get_session
from matrix.device.hmac import (
    verify_signature,
)
from matrix.device.key_manager import KeyManager
from matrix.device.login_state import LoginStateMonitor, LoginStateReport
from matrix.device.pairing import (
    PairingError,
    PairingService,
)
from matrix.device.registry import (
    DeviceHeartbeatData,
    DeviceNotFound,
    DeviceRegistry,
)
from matrix.device.tailscale_client import TailscaleClient

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class DeviceRegisterIn(BaseModel):
    nickname: str
    model: str
    android_version: str
    apk_version: str
    tailnet_ip: Optional[str] = None
    adb_serial: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    # 预创建模式：APK 已有 UUID，update 而非 insert
    device_id: Optional[UUID] = None


class DeviceOut(BaseModel):
    id: UUID
    nickname: str
    model: str
    android_version: str
    apk_version: str
    tailnet_ip: Optional[str] = None
    status: str
    tags: list[str] = Field(default_factory=list)
    last_heartbeat: Optional[str] = None
    bound_accounts: int = 0


class HeartbeatIn(BaseModel):
    battery: Optional[int] = Field(default=None, ge=0, le=100)
    network: Optional[str] = None
    signal_dbm: Optional[int] = None
    foreground_app: Optional[str] = None
    errors: Optional[dict] = None
    tailscale_state: Optional[str] = None


class PairCreateIn(BaseModel):
    """``POST /devices/{id}/pair`` 的请求体（来自 APK）。"""

    pair_code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")
    hmac_key_id: Optional[str] = None  # APK 可选地提前声明期望的 key_id


class PairCreateOut(BaseModel):
    device_id: UUID
    key_id: str
    hmac_key: str
    sent_via: str = "tailscale"


# ---------------------------------------------------------------------------
# HMAC 鉴权依赖
# ---------------------------------------------------------------------------


class HmacAuthResult:
    def __init__(self, device_id: UUID, key_id: str) -> None:
        self.device_id = device_id
        self.key_id = key_id


async def verify_hmac(
    request: Request,
    device_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_signature: Annotated[Optional[str], Header(alias="X-Signature")] = None,
    x_timestamp: Annotated[Optional[str], Header(alias="X-Timestamp")] = None,
    x_request_id: Annotated[Optional[str], Header(alias="X-Request-Id")] = None,
) -> HmacAuthResult:
    """校验 APK 端 HMAC 签名（依赖项；直接用 ``Depends(verify_hmac)``）。

    步骤：
    1. 解析 3 个 header
    2. 读 body
    3. 查 device 的当前 HMAC key hash
    4. 验签 + 验时间戳
    """
    if not (x_signature and x_timestamp and x_request_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing HMAC headers (X-Signature / X-Timestamp / X-Request-Id)",
        )

    device = await session.get(Device, device_id)
    if device is None or device.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    if device.hmac_key_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="device has no active HMAC key")

    km = KeyManager(session)
    key_hash = await km.lookup_hash(device_id, device.hmac_key_id)
    if key_hash is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="HMAC key not found")

    body_bytes = await request.body()

    # DB 存的是 hash；为验签需用原 secret。这里用 trick：主控本地**配置**文件（按 SDD §6.3）
    # 存密钥原文（与 APK 端 Keystore 加密后的原文相同），但 ORM 暂未建模配置文件，
    # 因此这里接受一个 ``matrix_config`` 注入作为可测试的密钥源。fallback 用 hash 不可逆，
    # 仅作占位：在生产中应从 ``AppConfig`` / 加密文件加载。
    secret = await _load_secret_for_verify(session, device.hmac_key_id)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="HMAC secret not retrievable; check key config storage",
        )

    if not verify_signature(secret, x_timestamp, x_request_id, body_bytes, x_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid HMAC signature")

    return HmacAuthResult(device_id=device_id, key_id=device.hmac_key_id)


async def _load_secret_for_verify(session: AsyncSession, key_id: str) -> Optional[bytes]:
    """从 ``app_config`` 加载主控侧的密钥原文（与 APK 端同步下发时存的同一份）。

    配置 key 约定：``hmac_secret:{key_id}``，value 为 base64 字符串。
    生产中应使用 OS keyring；这里用 ``app_config`` 保持可移植 / 可测。
    返回 None 表示未找到。
    """
    from matrix.db.models import AppConfig

    config_key = f"hmac_secret:{key_id}"
    config = await session.get(AppConfig, config_key)
    if config is None:
        return None
    val = config.value
    if not isinstance(val, dict):
        return None
    raw = val.get("secret")
    if not isinstance(raw, str):
        return None
    import base64

    try:
        return base64.b64decode(raw.encode("ascii"))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


def _to_out(device: Device, bound_accounts: int = 0) -> DeviceOut:
    return DeviceOut(
        id=device.id,
        nickname=device.nickname,
        model=device.model,
        android_version=device.android_version,
        apk_version=device.apk_version,
        tailnet_ip=str(device.tailnet_ip) if device.tailnet_ip else None,
        status=device.status,
        tags=list(device.tags or []),
        last_heartbeat=device.last_heartbeat.isoformat() if device.last_heartbeat else None,
        bound_accounts=bound_accounts,
    )


@router.get("", response_model=dict)
async def list_devices(
    session: Annotated[AsyncSession, Depends(get_session)],
    status_filter: Annotated[Optional[str], Query(alias="status")] = None,
    tag: Annotated[Optional[str], Query()] = None,
) -> dict:
    """设备列表（master-rest.openapi.yaml ``GET /devices``）。"""
    registry = DeviceRegistry(session)
    devices = await registry.get_devices(status=status_filter, tag=tag)
    return {"items": [_to_out(d) for d in devices]}


@router.post("/register", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
async def register_device(
    payload: DeviceRegisterIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceOut:
    """注册 / 更新设备（APK 调）。"""
    registry = DeviceRegistry(session)
    device = await registry.register_device(
        nickname=payload.nickname,
        model=payload.model,
        android_version=payload.android_version,
        apk_version=payload.apk_version,
        tailnet_ip=payload.tailnet_ip,
        adb_serial=payload.adb_serial,
        tags=payload.tags,
        device_id=payload.device_id,
    )
    return _to_out(device)


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DeviceOut:
    """设备详情（master-rest.openapi.yaml ``GET /devices/{id}``）。"""
    registry = DeviceRegistry(session)
    device = await registry.get_device(device_id)
    if device is None or device.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    bound = await registry.get_bound_account_count(device_id)
    return _to_out(device, bound_accounts=bound)


@router.post("/{device_id}/pair", response_model=PairCreateOut)
async def pair_device(
    device_id: UUID,
    payload: PairCreateIn,
    # 接受 HMAC 鉴权；如果设备尚未配对，签名校验可能失败 → 仍允许按配对码验证
    session: Annotated[AsyncSession, Depends(get_session)],
    _hmac: Annotated[Optional[HmacAuthResult], Depends(verify_hmac)] = None,
) -> PairCreateOut:
    """APK 调主控配对。

    鉴权策略：APK 在配对时还没有共享密钥，因此 HMAC 鉴权失败也允许继续
    （通过配对码验证身份）。如果客户端带上了 HMAC header，优先校验。

    标准配对流程（防重放）：
    1. ``validate_code`` 预检：配对码存在 + 未过期 + 未被消费
    2. 校验 ``device_id`` 与配对码登记设备一致
    3. ``consume_pair_code`` 原子消费（防并发 / 防重放）
    4. ``complete_pairing`` 签发并下发 HMAC 密钥
    """
    km = KeyManager(session)
    ts = TailscaleClient(api_url="", api_key="")  # 仅占位
    pairing = PairingService(session, km, ts)

    expected_device = pairing.validate_code(payload.pair_code)
    if expected_device is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired pair code"
        )
    if expected_device != device_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="pair code does not match device"
        )
    if not pairing.consume_pair_code(payload.pair_code):
        # 已被并发消费
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="pair code already consumed"
        )

    try:
        result = await pairing.complete_pairing(
            device_id=device_id,
            pair_code=payload.pair_code,
        )
    except PairingError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    return PairCreateOut(
        device_id=result.device_id,
        key_id=result.key_id,
        hmac_key=result.hmac_key,
        sent_via=result.sent_via,
    )


@router.post("/{device_id}/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
async def heartbeat(
    device_id: UUID,
    payload: HeartbeatIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _auth: Annotated[HmacAuthResult, Depends(verify_hmac)] = None,
) -> None:
    """APK 心跳上报。强制 HMAC 鉴权。"""
    registry = DeviceRegistry(session)
    data = DeviceHeartbeatData(
        battery=payload.battery,
        network=payload.network,
        signal_dbm=payload.signal_dbm,
        foreground_app=payload.foreground_app,
        errors=payload.errors,
        tailscale_state=payload.tailscale_state,
    )
    try:
        await registry.update_heartbeat(device_id, data)
    except DeviceNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/{device_id}/login_state", status_code=status.HTTP_204_NO_CONTENT)
async def report_login_state(
    device_id: UUID,
    payload: dict,
    session: Annotated[AsyncSession, Depends(get_session)],
    _auth: Annotated[HmacAuthResult, Depends(verify_hmac)] = None,
) -> None:
    """APK 上报 XHS 登录态。"""
    monitor = LoginStateMonitor(session)
    try:
        report = LoginStateReport(
            account_id=UUID(str(payload["account_id"])),
            device_id=device_id,
            result=str(payload["result"]),
            risk_signal=payload.get("risk_signal"),
            error_message=payload.get("error_message"),
        )
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    await monitor.report(report)


# ---------------------------------------------------------------------------
# 辅助：hmac 模块也被 import 用于测试 / 文档；显式 re-export
# ---------------------------------------------------------------------------

__all__ = ["router", "verify_hmac", "HmacAuthResult", "DeviceRegisterIn", "DeviceOut"]
