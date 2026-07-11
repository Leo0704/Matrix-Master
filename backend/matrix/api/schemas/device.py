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
    model: str
    android_version: str
    apk_version: Optional[str] = None
    tailnet_ip: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    status: DeviceStatus = "pending"
    last_heartbeat: Optional[datetime] = None
    bound_accounts: int = 0  # 严格 1 机 1 账号下 ≤ 1
    bound_account_handle: Optional[str] = None  # 绑定账号的 handle（1:1 下最多一个）
    pair_code: str | None = None


class DeviceRegisterRequest(BaseModel):
    nickname: str
    model: str
    android_version: str
    apk_version: str
    tailnet_ip: str
    adb_serial: Optional[str] = None


class DeviceUpdate(BaseModel):
    """局部更新 — 所有字段可选，None 表示该字段不动。"""

    nickname: Optional[str] = None
    tags: Optional[list[str]] = None


class DeviceUnbindResponse(BaseModel):
    """解绑设备返回：被解绑的账号 handle（如果有）。"""

    device_id: uuid.UUID
    unbound_account_handle: Optional[str] = None


class DevicePairRequest(BaseModel):
    pair_code: str = Field(..., description="6 位数字配对码")


class DevicePairResponse(BaseModel):
    key_id: str = Field(..., description="主控签发的密钥 ID")
    hmac_key: str = Field(..., description="base64 编码的 HMAC 密钥")


class DeviceListResponse(BaseModel):
    items: list[Device]
