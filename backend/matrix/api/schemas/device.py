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
    bound_accounts: int = 0


class DeviceRegisterRequest(BaseModel):
    nickname: str
    model: str
    android_version: str
    apk_version: str
    tailnet_ip: str
    adb_serial: Optional[str] = None


class DevicePairRequest(BaseModel):
    pair_code: str = Field(..., description="6 位数字配对码")
    hmac_key_id: str = Field(..., description="主控生成的密钥 ID")


class DevicePairResponse(BaseModel):
    hmac_key: str = Field(..., description="base64 编码的 HMAC 密钥")


class DeviceListResponse(BaseModel):
    items: list[Device]
