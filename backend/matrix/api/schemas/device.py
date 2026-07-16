"""Pydantic schemas — devices。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

DeviceStatus = Literal["pending", "active", "offline", "tailscale_degraded", "disabled"]


class Device(BaseModel):
    id: uuid.UUID
    nickname: str
    # P2-3：4 个身份字段全部 Optional —— APK 上线前回填之前都可能是 None
    model: Optional[str] = None
    android_version: Optional[str] = None
    apk_version: Optional[str] = None
    tailnet_ip: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    status: DeviceStatus = "pending"
    last_heartbeat: Optional[datetime] = None
    bound_accounts: int = 0  # 严格 1 机 1 账号下 ≤ 1
    bound_account_handle: Optional[str] = None  # 绑定账号的 handle（1:1 下最多一个）
    pair_code: str | None = None
    business_id: uuid.UUID  # v0.7+ 业务模型重构：设备挂业务名下


class DeviceRegisterRequest(BaseModel):
    """P2-3：注册时只需昵称 + adb_serial + business_id，其余身份信息由 APK 配对后回填。"""

    nickname: str
    adb_serial: Optional[str] = None
    business_id: uuid.UUID  # v0.7+ 业务模型重构：必填


class DevicePairIdentity(BaseModel):
    """APK 配对时上报的设备身份，4 字段全 Optional —— 老 APK 不报也能配对。"""

    model: Optional[str] = None
    android_version: Optional[str] = None
    apk_version: Optional[str] = None
    tailnet_ip: Optional[str] = None


class DeviceUpdate(BaseModel):
    """局部更新 — 所有字段可选，None 表示该字段不动。"""

    nickname: Optional[str] = None
    tags: Optional[list[str]] = None


class DeviceUnbindResponse(BaseModel):
    """解绑设备返回：被解绑的账号 handle（如果有）。"""

    device_id: uuid.UUID
    unbound_account_handle: Optional[str] = None


class DevicePairRequest(BaseModel):
    """消费配对码并下发 HMAC 密钥。可选 ``identity`` 块用于 APK 主动回传真实身份。"""

    pair_code: str = Field(..., description="6 位数字配对码")
    identity: Optional[DevicePairIdentity] = None


class DevicePairResponse(BaseModel):
    key_id: str = Field(..., description="主控签发的密钥 ID")
    hmac_key: str = Field(..., description="base64 编码的 HMAC 密钥")
    # P2-1 测试期：admin 触发生成配对码的 endpoint 需要把配对码回在响应里
    # （老 pairDevice 路由不返回，正常 pair 流程不需要）。Optional 兼容
    # 老的 pairDevice 调用。
    pair_code: str | None = Field(default=None, description="P2-1 测试用：admin-issued 时返回 6 位配对码")


class DeviceListResponse(BaseModel):
    items: list[Device]
