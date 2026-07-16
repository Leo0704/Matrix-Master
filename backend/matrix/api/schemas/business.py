"""Pydantic schemas — businesses（v0.7+ 业务模型重构）。

业务是项目根，所有核心资源挂在业务名下。
业务支持软归档（status='archived'），不删行。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


BusinessStatus = Literal["active", "archived"]


class Business(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str] = None
    status: BusinessStatus = "active"
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime] = None


class BusinessCreate(BaseModel):
    """建业务：name / slug / description 必填。

    slug 全局 UNIQUE（路由前缀 + 脚本引用锚点）。
    """

    name: str = Field(..., min_length=1, max_length=64)
    slug: str = Field(..., min_length=1, max_length=64)
    description: Optional[str] = None


class BusinessUpdate(BaseModel):
    """局部更新 — 所有字段可选，None 表示该字段不动。

    status 不暴露（改状态走独立 /archive /unarchive 端点，语义清晰）。
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = None


class BusinessListResponse(BaseModel):
    items: list[Business]
    total: int = 0


__all__ = [
    "Business",
    "BusinessCreate",
    "BusinessUpdate",
    "BusinessListResponse",
    "BusinessStatus",
]