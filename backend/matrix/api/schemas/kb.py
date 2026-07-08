"""Pydantic schemas — 知识库 (kb)。

商品事实库 (type=product) 与原有 brand/persona/rule 等复用同一套 schema。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


KbType = Literal[
    "brand", "persona", "rule", "topic", "history", "template", "product"
]


class KbDocument(BaseModel):
    id: uuid.UUID
    type: KbType
    ref_id: Optional[uuid.UUID] = None
    title: Optional[str] = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    is_published: bool = False
    created_at: datetime
    updated_at: datetime


class KbDocumentCreate(BaseModel):
    type: KbType
    content: str
    title: Optional[str] = None
    ref_id: Optional[uuid.UUID] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_published: bool = False


class KbDocumentUpdate(BaseModel):
    content: Optional[str] = None
    title: Optional[str] = None
    ref_id: Optional[uuid.UUID] = None
    metadata: Optional[dict[str, Any]] = None
    is_published: Optional[bool] = None


class KbDocumentListResponse(BaseModel):
    items: list[KbDocument]
    total: int = 0


class KbSearchRequest(BaseModel):
    query: str
    type: KbType
    top_k: int = 5
    filters: Optional[dict[str, Any]] = None


class KbSearchHit(BaseModel):
    chunk_id: uuid.UUID
    doc_id: uuid.UUID
    doc_type: KbType
    doc_title: Optional[str]
    chunk_index: int
    text: str
    score: float
    sources: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KbSearchResponse(BaseModel):
    items: list[KbSearchHit]


class KbPublishRequest(BaseModel):
    reviewer: str = Field(..., min_length=1)
    comment: Optional[str] = None


class KbPublishResponse(BaseModel):
    doc_id: uuid.UUID
    is_published: bool


__all__ = [
    "KbType",
    "KbDocument",
    "KbDocumentCreate",
    "KbDocumentUpdate",
    "KbDocumentListResponse",
    "KbSearchRequest",
    "KbSearchHit",
    "KbSearchResponse",
    "KbPublishRequest",
    "KbPublishResponse",
]
