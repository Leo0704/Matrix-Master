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
    persona_id: Optional[uuid.UUID] = None  # 可选：不绑定则写笔记时从知识库检索
    business_id: uuid.UUID  # v0.7+ 业务模型重构：必填


class AccountUpdate(BaseModel):
    """局部更新 — 所有字段可选，None 表示该字段不动。

    device_id 传新设备 ID = 换绑（受 1:1 唯一约束，重复会 409）。
    解绑请走独立 ``POST /accounts/{id}/unbind-device`` 端点（语义清晰）。

    status 支持人工激活/停用账号：
    pending→active、active→suspended、suspended→active、任意非 banned→disabled。
    banned 状态由平台风险信号驱动，不允许 API 直改。
    """

    handle: Optional[str] = None
    persona_id: Optional[uuid.UUID] = None
    device_id: Optional[uuid.UUID] = None
    status: Optional[AccountStatus] = None


class AccountListResponse(BaseModel):
    items: list[Account]
