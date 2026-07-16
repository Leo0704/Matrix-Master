"""Pydantic schemas — accounts。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

AccountStatus = Literal["pending", "active", "suspended", "banned", "disabled"]


class Account(BaseModel):
    id: uuid.UUID
    handle: str
    persona_id: Optional[uuid.UUID] = None
    device_id: Optional[uuid.UUID] = None
    business_id: uuid.UUID  # v0.7+ 业务模型重构：账号绑死业务
    status: AccountStatus = "pending"
    last_active: Optional[datetime] = None
    risk_score: float = Field(0.0, ge=0.0, le=1.0)


class AccountCreate(BaseModel):
    handle: str
    device_id: uuid.UUID
    persona_id: uuid.UUID
    business_id: uuid.UUID  # v0.7+ 业务模型重构：必填


class AccountUpdate(BaseModel):
    """局部更新 — 所有字段可选，None 表示该字段不动。

    device_id 传新设备 ID = 换绑（受 1:1 唯一约束，重复会 409）。
    解绑请走独立 ``POST /accounts/{id}/unbind-device`` 端点（语义清晰）。
    """

    handle: Optional[str] = None
    persona_id: Optional[uuid.UUID] = None
    device_id: Optional[uuid.UUID] = None


class AccountListResponse(BaseModel):
    items: list[Account]
