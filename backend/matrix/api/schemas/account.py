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
    status: AccountStatus = "pending"
    last_active: Optional[datetime] = None
    risk_score: float = Field(0.0, ge=0.0, le=1.0)


class AccountCreate(BaseModel):
    handle: str
    device_id: uuid.UUID
    persona_id: uuid.UUID


class AccountListResponse(BaseModel):
    items: list[Account]
