"""设备注册 / 心跳 / 查询（SDD §3.5.1 + master-rest.openapi.yaml ``/devices``）。

- ``register_device``: APK 端首次注册（pre-create 模式：UI 触发添加后预创建 device 行，
  APK 启动时 update tailnet_ip / status）
- ``unregister_device``: 设备下线（soft delete + revoke HMAC keys）
- ``update_heartbeat``: 写 ``device_heartbeats`` 表
- ``get_devices``: 按 status / tag 过滤查询
- ``group_by_tag``: 按 ``devices.tags`` 分组
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Device, DeviceHeartbeat
from matrix.device.key_manager import KeyManager

logger = get_logger(__name__)


@dataclass
class DeviceHeartbeatData:
    """APK 端上报的心跳数据。"""

    battery: Optional[int] = None
    network: Optional[str] = None  # "4G" / "5G" / "none"
    signal_dbm: Optional[int] = None
    foreground_app: Optional[str] = None
    errors: Optional[dict] = None
    tailscale_state: Optional[str] = None


class DeviceNotFound(LookupError):
    """device_id 不存在。"""


class DeviceRegistry:
    """设备注册 / 查询服务。"""

    def __init__(self, session: AsyncSession, key_manager: Optional[KeyManager] = None) -> None:
        self.session = session
        self.key_manager = key_manager

    # ------------------------------------------------------------------
    # 注册 / 下线
    # ------------------------------------------------------------------

    async def register_device(
        self,
        nickname: str,
        model: str,
        android_version: str,
        apk_version: str,
        tailnet_ip: Optional[str] = None,
        adb_serial: Optional[str] = None,
        tags: Optional[list[str]] = None,
        device_id: Optional[UUID] = None,
    ) -> Device:
        """注册 / 预创建一台设备。

        - 传 ``device_id`` 时：APK 已有 UUID，update 已存在的预创建行（status -> active，填 tailnet_ip）。
        - 不传 ``device_id`` 时：APK 第一次注册，DB 创建设备行（status=pending）。
        """
        if device_id is not None:
            existing = await self.session.get(Device, device_id)
            if existing is not None:
                existing.nickname = nickname
                existing.model = model
                existing.android_version = android_version
                existing.apk_version = apk_version
                existing.tailnet_ip = tailnet_ip or existing.tailnet_ip
                if adb_serial is not None:
                    existing.adb_serial = adb_serial
                if tags is not None:
                    existing.tags = list(tags)
                # 首次 tailnet_ip 上报即转 active
                if existing.status == "pending" and tailnet_ip:
                    existing.status = "active"
                existing.last_heartbeat = datetime.now(timezone.utc)
                existing.updated_at = datetime.now(timezone.utc)
                await self.session.flush()
                return existing

        device = Device(
            id=device_id or uuid4(),
            nickname=nickname,
            model=model,
            android_version=android_version,
            apk_version=apk_version,
            tailnet_ip=tailnet_ip,
            adb_serial=adb_serial,
            tags=list(tags or []),
            status="active" if tailnet_ip else "pending",
        )
        self.session.add(device)
        await self.session.flush()
        logger.info("device.registered", device_id=str(device.id))
        return device

    async def unregister_device(self, device_id: UUID) -> bool:
        """设备下线：撤销 HMAC key + soft delete。

        Returns:
            True 表示下线成功；False 表示设备不存在。
        """
        device = await self.session.get(Device, device_id)
        if device is None:
            return False

        if self.key_manager is not None:
            await self.key_manager.revoke_all(device_id)

        # 立即撤销当前 active key id 引用 + 标记 disabled
        device.status = "disabled"
        device.hmac_key_id = None
        device.deleted_at = datetime.now(timezone.utc)
        device.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        logger.info("device.unregistered", device_id=str(device_id))
        return True

    # ------------------------------------------------------------------
    # 心跳
    # ------------------------------------------------------------------

    async def update_heartbeat(
        self,
        device_id: UUID,
        data: DeviceHeartbeatData,
    ) -> None:
        """写心跳到 ``device_heartbeats`` 表 + 更新 ``devices.last_heartbeat``。"""
        device = await self.session.get(Device, device_id)
        if device is None:
            raise DeviceNotFound(f"device {device_id} not found")

        ts = datetime.now(timezone.utc)
        # 验证 network 取值在 DeviceHeartbeat 允许范围内
        if data.network and data.network not in ("4G", "5G", "none", "wifi", "ethernet"):
            data.network = "none"

        hb = DeviceHeartbeat(
            device_id=device_id,
            ts=ts,
            battery=data.battery,
            network=data.network,
            signal_dbm=data.signal_dbm,
            foreground_app=data.foreground_app,
            errors=data.errors,
            tailscale_state=data.tailscale_state,
        )
        self.session.add(hb)

        device.last_heartbeat = ts
        # tailscale_state=disconnected 时降级状态（不直接 offline）
        if data.tailscale_state == "disconnected" and device.status == "active":
            device.status = "tailscale_degraded"
        elif data.tailscale_state == "connected" and device.status == "tailscale_degraded":
            device.status = "active"
        device.updated_at = ts
        await self.session.flush()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def get_device(self, device_id: UUID) -> Optional[Device]:
        return await self.session.get(Device, device_id)

    async def get_devices(
        self,
        status: Optional[str] = None,
        tag: Optional[str] = None,
        include_deleted: bool = False,
    ) -> list[Device]:
        """按 status / tag 过滤；默认排除已删除（``deleted_at IS NULL``）。"""
        conditions = []
        if not include_deleted:
            conditions.append(Device.deleted_at.is_(None))
        if status:
            conditions.append(Device.status == status)
        if tag:
            # PG ARRAY contains
            conditions.append(Device.tags.contains([tag]))

        stmt = select(Device)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(Device.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def group_by_tag(self) -> dict[str, list[Device]]:
        """按 ``devices.tags`` 全集分组（一个设备可出现在多个 group 中）。"""
        devices = await self.get_devices()
        groups: dict[str, list[Device]] = {}
        for d in devices:
            for t in d.tags or []:
                groups.setdefault(t, []).append(d)
        return groups

    async def get_bound_account_count(self, device_id: UUID) -> int:
        """返回绑定到该设备的活跃账号数（status != 'disabled'）。"""
        from matrix.db.models import Account

        result = await self.session.execute(
            select(Account).where(
                Account.device_id == device_id,
                Account.deleted_at.is_(None),
            )
        )
        accounts = list(result.scalars().all())
        return len([a for a in accounts if a.status != "disabled"])


__all__ = [
    "DeviceRegistry",
    "DeviceHeartbeatData",
    "DeviceNotFound",
]
