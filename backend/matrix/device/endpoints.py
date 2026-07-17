"""生产 APK endpoint 解析与主控侧 HMAC 密钥读取。"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.db.models import AppConfig, Device


@dataclass(frozen=True)
class ApkEndpoint:
    """某设备在 tailnet 上的 APK 地址和当前共享密钥。"""

    base_url: str
    hmac_key: bytes


class DeviceEndpointResolver:
    """从设备记录和受保护的内部配置中构造 APK endpoint。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], port: int = 8765) -> None:
        self._session_factory = session_factory
        self._port = port

    async def __call__(self, device_id: UUID) -> ApkEndpoint:
        async with self._session_factory() as session:
            device = await session.get(Device, device_id)
            if device is None or device.deleted_at is not None:
                raise LookupError(f"device {device_id} not found")
            if not device.tailnet_ip:
                raise RuntimeError(f"device {device_id} has no tailnet IP")
            if not device.hmac_key_id:
                raise RuntimeError(f"device {device_id} has no active HMAC key")

            secret_row = await session.get(AppConfig, f"hmac_secret:{device.hmac_key_id}")
            secret_b64 = (secret_row.value or {}).get("secret") if secret_row else None
            if not isinstance(secret_b64, str):
                raise RuntimeError(f"device {device_id} HMAC secret is unavailable")
            try:
                secret = base64.b64decode(secret_b64, validate=True)
            except (ValueError, TypeError) as exc:
                raise RuntimeError(f"device {device_id} HMAC secret is invalid") from exc
            if not secret:
                raise RuntimeError(f"device {device_id} HMAC secret is empty")

            # dev 环境（macOS Docker Desktop）下容器无法直连手机 WiFi IP；
            # 经 adb forward + host.docker.internal 才可达。设环境变量
            # MATRIX_DEV_APK_HOST=host.docker.internal 即覆盖 tailnet_ip。
            host = os.environ.get("MATRIX_DEV_APK_HOST") or device.tailnet_ip
            return ApkEndpoint(base_url=f"http://{host}:{self._port}", hmac_key=secret)


__all__ = ["ApkEndpoint", "DeviceEndpointResolver"]
