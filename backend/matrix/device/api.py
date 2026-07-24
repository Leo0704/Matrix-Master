"""设备-账号管理子系统的 FastAPI 路由（按 master-rest.openapi.yaml ``/devices``）。

HMAC 鉴权：心跳 / 登录态上报 / 任务拉取与完成等 APK 端点需带
``X-Signature`` / ``X-Timestamp`` / ``X-Request-Id`` 三个 header；
签名内容 ``{timestamp}\\n{request_id}\\n{body_sha256}``。
配对（pair）不在本 router——见 ``matrix.api.routes.devices``：配对阶段
APK 尚无共享密钥，密码学上无法验签，由一次性配对码 + 失败限流保护。
"""
from __future__ import annotations

from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Device, Note, Task
from matrix.db.session import get_session_factory


async def get_session():
    """与 matrix.api.deps.get_db 等价的本地副本。直接定义而非 import 是为避免
    ``device → api.deps → api.app → device`` 的循环 import（uvicorn 以
    ``matrix.api.app`` 为入口时顺序碰巧不炸，但独立 import / 测试会崩）。"""
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
from matrix.device.hmac import (
    verify_signature,
)
from matrix.device.key_manager import KeyManager
from matrix.device.login_state import LoginStateError, LoginStateMonitor

from matrix.device.registry import (
    DeviceHeartbeatData,
    DeviceNotFound,
    DeviceRegistry,
)

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class HeartbeatIn(BaseModel):
    battery: Optional[int] = Field(default=None, ge=0, le=100)
    network: Optional[str] = None
    signal_dbm: Optional[int] = None
    foreground_app: Optional[str] = None
    errors: Optional[dict] = None
    tailscale_state: Optional[str] = None
    tailscale_ip: Optional[str] = None


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
    if device.status == "disabled":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="device is retired",
        )
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

    配置 key 约定：``hmac_secret:{key_id}``。
    新格式（v1）：``{"v": 1, "enc_secret": "<fernet token>"}``——信封加密存储，
    主密钥见 ``matrix.device.secret_box``；
    旧格式：``{"secret": "<base64 明文>"}``——明文落库时代的遗留行，读到时按
    明文返回并顺手重写为新格式（懒迁移；重写失败不影响本次验签，下次请求重试）。
    返回 None 表示未找到或解密失败。
    """
    import base64

    from matrix.db.models import AppConfig
    from matrix.device.secret_box import decrypt_secret, encrypt_secret

    config_key = f"hmac_secret:{key_id}"
    config = await session.get(AppConfig, config_key)
    if config is None:
        return None
    val = config.value
    if not isinstance(val, dict):
        return None

    enc = val.get("enc_secret")
    if isinstance(enc, str):
        return decrypt_secret(enc)

    raw = val.get("secret")
    if not isinstance(raw, str):
        return None
    try:
        secret = base64.b64decode(raw.encode("ascii"))
    except Exception:
        return None
    try:
        config.value = {"v": 1, "enc_secret": encrypt_secret(secret)}
    except Exception:
        pass  # 懒迁移重写失败无碍本次验签，下次请求重试
    return secret


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


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
        tailscale_ip=payload.tailscale_ip,
    )
    try:
        await registry.update_heartbeat(device_id, data)
    except DeviceNotFound as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


class LoginStateIn(BaseModel):
    """APK 登录态上报体（新契约）：不带 account_id——APK 无权访问控制台鉴权的
    GET /accounts，账号由后端按 ``accounts.device_id`` 解析。"""

    platform: Optional[str] = None  # 目前仅 xhs；accounts 表尚无 platform 列，仅作契约透传
    state: str  # 取值同 VALID_RESULTS：success / failed / captcha / logout / expired
    risk_signal: Optional[str] = None
    error_message: Optional[str] = None


@router.post("/{device_id}/login_state", status_code=status.HTTP_204_NO_CONTENT)
async def report_login_state(
    device_id: UUID,
    payload: LoginStateIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    _auth: Annotated[HmacAuthResult, Depends(verify_hmac)] = None,
) -> None:
    """APK 上报 XHS 登录态。绑定账号由后端按 device_id 解析。"""
    monitor = LoginStateMonitor(session)
    try:
        await monitor.report_for_device(
            device_id,
            payload.state,
            risk_signal=payload.risk_signal,
            error_message=payload.error_message,
        )
    except LoginStateError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e


# ---------------------------------------------------------------------------
# 任务拉取 / 完成（v0.7 Phase 6：手机主动拉取任务模型）
# ---------------------------------------------------------------------------


class TaskNextResponse(BaseModel):
    id: UUID
    action: str
    payload: dict
    request_id: str


class TaskCompleteIn(BaseModel):
    ok: bool
    platform_note_id: Optional[str] = None
    platform_url: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@router.post("/{device_id}/tasks/next", response_model=dict)
async def claim_next_task(
    device_id: UUID,
    _auth: Annotated[HmacAuthResult, Depends(verify_hmac)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """手机认领下一条待执行任务。

    原子语义：用 CTE + FOR UPDATE SKIP LOCKED 选一条 pending task，
    同一台设备并发请求不会重复消费同一条任务。
    只认领 ``action='device_publish'``：手机只会执行发布，collect 等
    其他动作由后端 worker 处理，被手机抢走只会判死。
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    stmt = text(
        """
        WITH next_task AS (
            SELECT id FROM tasks
            WHERE device_id = :device_id
              AND status = 'pending'
              AND action = 'device_publish'
              AND scheduled_at <= :now
            ORDER BY scheduled_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE tasks
        SET status = 'running',
            attempts = attempts + 1,
            executed_at = :now
        FROM next_task
        WHERE tasks.id = next_task.id
        RETURNING tasks.id, tasks.action, tasks.payload, tasks.request_id
        """
    )
    result = await session.execute(
        stmt, {"device_id": str(device_id), "now": now}
    )
    row = result.mappings().one_or_none()
    if row is None:
        return {"ok": True, "data": None}
    return {
        "ok": True,
        "data": TaskNextResponse(
            id=row["id"],
            action=row["action"],
            payload=dict(row["payload"] or {}),
            request_id=row["request_id"],
        ),
    }


@router.post("/{device_id}/tasks/{task_id}/complete", response_model=dict)
async def complete_task(
    device_id: UUID,
    task_id: UUID,
    body: TaskCompleteIn,
    _auth: Annotated[HmacAuthResult, Depends(verify_hmac)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """手机上报任务执行结果。

    成功后若 action 为 device_publish，会原子地把发布结果写回 notes 表
    （platform_note_id / platform_url / published_at）。
    """
    from datetime import UTC, datetime

    task = await session.get(Task, task_id)
    if task is None or task.device_id != device_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    if task.status != "running":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"task is {task.status}, cannot complete",
        )

    now = datetime.now(UTC)
    task.status = "success" if body.ok else "failed"
    task.executed_at = now
    if not body.ok:
        task.last_error = {
            "code": body.error_code or "UNKNOWN",
            "message": body.error_message or "",
        }

    if body.ok and task.action == "device_publish":
        note_id_str = (task.payload or {}).get("note_id")
        if note_id_str:
            try:
                note = await session.get(Note, UUID(str(note_id_str)))
            except (ValueError, TypeError):
                note = None
            if note is not None:
                note.status = "published"
                note.platform_note_id = body.platform_note_id
                note.platform_url = body.platform_url
                note.published_at = now

    await session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# 辅助：hmac 模块也被 import 用于测试 / 文档；显式 re-export
# ---------------------------------------------------------------------------

__all__ = [
    "router",
    "verify_hmac",
    "HmacAuthResult",
    "claim_next_task",
    "complete_task",
]
