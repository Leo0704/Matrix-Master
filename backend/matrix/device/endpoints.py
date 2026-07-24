"""生产 APK endpoint 解析与主控侧 HMAC 密钥读取。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.db.models import Device
from matrix.device.api import _load_secret_for_verify


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

            # 与 verify_hmac 共用同一份解密逻辑：新格式信封加密
            # （{"v":1,"enc_secret":...}），旧格式明文读到时懒迁移。
            secret = await _load_secret_for_verify(session, device.hmac_key_id)
            if secret is None:
                raise RuntimeError(f"device {device_id} HMAC secret is unavailable")

            # dev 环境（macOS Docker Desktop）下容器无法直连手机 WiFi IP；
            # 经 adb forward + host.docker.internal 才可达。设环境变量
            # MATRIX_DEV_APK_HOST=host.docker.internal 即覆盖 tailnet_ip。
            host = os.environ.get("MATRIX_DEV_APK_HOST") or device.tailnet_ip
            return ApkEndpoint(base_url=f"http://{host}:{self._port}", hmac_key=secret)


__all__ = ["ApkEndpoint", "DeviceEndpointResolver"]
