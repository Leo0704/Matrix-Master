"""Pydantic schemas — alerts。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

AlertSeverity = Literal["critical", "warning", "info"]


class AlertItem(BaseModel):
    id: uuid.UUID
    code: str
    severity: AlertSeverity
    message: str
    subject_id: Optional[str] = None
    resolved: bool = False
    created_at: datetime
    resolved_at: Optional[datetime] = None
    business_id: Optional[uuid.UUID] = None  # v0.7+ 业务归属（018 migration 加列）


class AlertListResponse(BaseModel):
    items: list[AlertItem]
    total: int = 0


class AlertResolveRequest(BaseModel):
    resolver: str = Field(..., min_length=1)
    comment: Optional[str] = None


class AlertResolveResponse(BaseModel):
    id: uuid.UUID
    resolved: bool


__all__ = [
    "AlertItem",
    "AlertListResponse",
    "AlertResolveRequest",
    "AlertResolveResponse",
    "AlertSeverity",
]
