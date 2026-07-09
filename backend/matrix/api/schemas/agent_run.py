"""Pydantic schemas — agent runs。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

AgentRunStatus = Literal["running", "success", "failed", "cancelled", "timeout"]


class AgentRun(BaseModel):
    id: uuid.UUID
    goal_id: Optional[uuid.UUID] = None
    current_state: str
    status: AgentRunStatus = "running"
    started_at: datetime
    updated_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None


class AgentRunListResponse(BaseModel):
    items: list[AgentRun]
