"""互动（v0.6）HTTP 响应的 Pydantic schemas。

只读 API：写入由 ``interact_node`` 通过 ``services.interaction_writer`` 完成。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


InteractionType = Literal["like", "comment", "follow", "share", "collect"]
InteractionResult = Literal["pending", "success", "failed"]


class Interaction(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    target_note_id: Optional[uuid.UUID] = None
    target_user: Optional[str] = None
    type: InteractionType
    content: Optional[str] = None
    ts: datetime
    result: InteractionResult
    error_message: Optional[str] = None
    request_id: Optional[str] = None


class InteractionListResponse(BaseModel):
    items: list[Interaction]
    total: int = Field(ge=0)


__all__ = [
    "Interaction",
    "InteractionListResponse",
    "InteractionType",
    "InteractionResult",
]
