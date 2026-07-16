"""Pydantic schemas — personas。"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class Persona(BaseModel):
    id: uuid.UUID
    name: str
    tone: str
    style_guide: str
    forbidden_words: list[str] = Field(default_factory=list)
    sample_note_ids: list[uuid.UUID] = Field(default_factory=list)
    version: int = 1
    business_id: uuid.UUID  # v0.7+ 业务模型重构：人设绑死业务，跨业务允许重名


class PersonaCreate(BaseModel):
    name: str
    tone: str
    style_guide: str
    forbidden_words: list[str] = Field(default_factory=list)
    sample_note_ids: list[uuid.UUID] = Field(default_factory=list)
    business_id: uuid.UUID  # v0.7+ 业务模型重构：必填


class PersonaUpdate(BaseModel):
    """局部更新 — 所有字段可选，None 表示该字段不动。"""

    name: Optional[str] = None
    tone: Optional[str] = None
    style_guide: Optional[str] = None
    forbidden_words: Optional[list[str]] = None
    sample_note_ids: Optional[list[uuid.UUID]] = None


class PersonaListResponse(BaseModel):
    items: list[Persona]
