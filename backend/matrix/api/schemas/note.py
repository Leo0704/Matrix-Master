"""Pydantic schemas — notes。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

NoteStatus = Literal[
    "draft",
    "reviewing",
    "scheduled",
    "publishing",
    "published",
    "failed",
    "deleted",
]


class Note(BaseModel):
    id: uuid.UUID
    # v0.7 Phase 5：DRAFT 阶段草稿先落库时 account_id 未知，绑账号等 DISPATCH 成功后才填
    account_id: Optional[uuid.UUID] = None
    title: str
    content: str
    images: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: NoteStatus = "draft"
    platform_note_id: Optional[str] = None
    platform_url: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    published_at: Optional[datetime] = None


class NoteCreate(BaseModel):
    account_id: Optional[uuid.UUID] = None
    title: str
    content: str
    images: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    status: NoteStatus = "draft"
    scheduled_at: Optional[datetime] = None


class NoteUpdate(BaseModel):
    """局部更新 — 所有字段可选，None 表示该字段不动。"""

    title: Optional[str] = None
    content: Optional[str] = None
    images: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    status: Optional[NoteStatus] = None
    scheduled_at: Optional[datetime] = None


class NoteListResponse(BaseModel):
    items: list[Note]
    total: int = 0
