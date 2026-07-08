"""HMAC 密钥生命周期管理（按 threat-model.md §6.3）。

- 生成：256 bit 随机（``hmac.generate_key``）
- 下发：仅一次，明文用完即弃（调用方负责不在进程内缓存）
- 轮换：每月自动生成新 key_id
- 撤销：设备下线时立即删除 / revoke
- 持久化：DB 只存 ``key_hash``（SHA-256）+ ``key_id``
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Device, DeviceHmacKey
from matrix.device.hmac import generate_key, hash_key

logger = logging.getLogger(__name__)

# 默认轮换周期：30 天
DEFAULT_ROTATION_DAYS = 30


@dataclass
class IssuedKey:
    """密钥下发结果。

    Attributes:
        key_id: 主控生成的可读 key_id
        secret: 明文密钥（仅下发这一次，调用方需立即丢弃明文）
    """

    key_id: str
    secret: bytes


class KeyManager:
    """HMAC 密钥管理器（DB 持久化）。

    使用方式：每台设备持有一个当前 active 的 key（``DeviceHmacKey``）；
    轮换 / 撤销时通过 ``rotated_at`` / ``revoked_at`` 字段记录。
    """

    def __init__(
        self,
        session: AsyncSession,
        rotation_days: int = DEFAULT_ROTATION_DAYS,
    ) -> None:
        self.session = session
        self.rotation_days = rotation_days

    @staticmethod
    def new_key_id() -> str:
        """生成新的 key_id（32 hex 字符，前缀 ``hmk_``）。"""
        return "hmk_" + secrets.token_hex(12)

    async def issue_key(self, device_id: UUID) -> IssuedKey:
        """为设备签发一个新 HMAC 密钥，并持久化到 DB。

        下发流程：调用方把 ``secret`` 走 Tailscale 通道发给 APK，
        APK 收到后立即用 Keystore 加密保存；调用方本函数必须不在进程内缓存明文。
        """
        key_id = self.new_key_id()
        secret = generate_key()
        key_hash = hash_key(secret)

        record = DeviceHmacKey(
            id=key_id,
            device_id=device_id,
            key_hash=key_hash,
        )
        self.session.add(record)

        # 同步 device 行的 hmac_key_id 字段
        await self.session.execute(
            update(Device)
            .where(Device.id == device_id)
            .values(hmac_key_id=key_id, updated_at=datetime.now(timezone.utc))
        )
        await self.session.flush()

        logger.info("hmac key issued", extra={"device_id": str(device_id), "key_id": key_id})
        return IssuedKey(key_id=key_id, secret=secret)

    async def lookup_hash(self, device_id: UUID, key_id: str) -> Optional[bytes]:
        """查找设备的某个 key_id 对应的 hash。返回 None 表示未找到或已撤销。"""
        result = await self.session.execute(
            select(DeviceHmacKey.key_hash).where(
                DeviceHmacKey.device_id == device_id,
                DeviceHmacKey.id == key_id,
                DeviceHmacKey.revoked_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def current_key_id(self, device_id: UUID) -> Optional[str]:
        """获取设备当前 active 的 key_id（未撤销的最近一条）。"""
        result = await self.session.execute(
            select(DeviceHmacKey.id)
            .where(
                DeviceHmacKey.device_id == device_id,
                DeviceHmacKey.revoked_at.is_(None),
            )
            .order_by(DeviceHmacKey.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def revoke_key(self, device_id: UUID, key_id: str) -> bool:
        """撤销设备的指定密钥（设备下线时调用）。

        Returns:
            True 表示至少一行被更新；False 表示 key_id 不存在或已撤销。
        """
        result = await self.session.execute(
            update(DeviceHmacKey)
            .where(
                DeviceHmacKey.device_id == device_id,
                DeviceHmacKey.id == key_id,
                DeviceHmacKey.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        return result.rowcount > 0

    async def revoke_all(self, device_id: UUID) -> int:
        """撤销某设备的所有未撤销密钥。返回受影响的行数。"""
        result = await self.session.execute(
            update(DeviceHmacKey)
            .where(
                DeviceHmacKey.device_id == device_id,
                DeviceHmacKey.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        return int(result.rowcount or 0)

    async def rotate_if_expired(self, device_id: UUID) -> Optional[IssuedKey]:
        """如果设备当前 key 超过 ``rotation_days``，自动轮换。

        调度层每天调一次即可；返回新 ``IssuedKey`` 表示触发了轮换，None 表示未轮换。
        """
        result = await self.session.execute(
            select(DeviceHmacKey)
            .where(
                DeviceHmacKey.device_id == device_id,
                DeviceHmacKey.revoked_at.is_(None),
            )
            .order_by(DeviceHmacKey.created_at.desc())
            .limit(1)
        )
        current = result.scalar_one_or_none()
        if current is None:
            return None

        age = datetime.now(timezone.utc) - current.created_at
        if age < timedelta(days=self.rotation_days):
            return None

        # 旧 key 标记轮换时间（保留一段时间供 APK 验证过渡）；并发签发新 key
        await self.session.execute(
            update(DeviceHmacKey)
            .where(DeviceHmacKey.id == current.id)
            .values(rotated_at=datetime.now(timezone.utc))
        )
        return await self.issue_key(device_id)


__all__ = ["IssuedKey", "KeyManager", "DEFAULT_ROTATION_DAYS"]
