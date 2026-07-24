"""Pydantic schemas — notifications (Phase 1 反向反馈通道)。

不同于 alerts（监控/resolved 二态）：本表 severity 含 success，
read_at 表示"已读"，可选 typed FK 用于过滤和跳详情。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

NotificationSeverity = Literal["info", "success", "warning", "error"]


class NotificationItem(BaseModel):
    id: uuid.UUID
    recipient: str
    code: str
    severity: NotificationSeverity
    title: str
    body: str
    goal_id: Optional[uuid.UUID] = None
    run_id: Optional[uuid.UUID] = None
    note_id: Optional[uuid.UUID] = None
    device_id: Optional[uuid.UUID] = None
    payload: dict = Field(default_factory=dict)
    read_at: Optional[datetime] = None
    created_at: datetime
    business_id: Optional[uuid.UUID] = None  # v0.7+ 业务归属（015/017 加列）
    # v0.7+ 消息可读化：关联实体的名称，减少前端二次查询
    goal_name: Optional[str] = None
    note_title: Optional[str] = None
    device_name: Optional[str] = None


class NotificationListResponse(BaseModel):
    items: list[NotificationItem]
    total: int = 0


class NotificationMarkReadRequest(BaseModel):
    """ids 为 None 表示把所有未读一次性全部标已读。"""

    ids: Optional[list[uuid.UUID]] = None
    # 业务约束（可选）：传了就只动本业务的通知（W5 业务隔离）
    business_id: Optional[uuid.UUID] = None


class NotificationMarkReadResponse(BaseModel):
    marked: int


class NotificationDeleteResponse(BaseModel):
    deleted: int


__all__ = [
    "NotificationItem",
    "NotificationListResponse",
    "NotificationMarkReadRequest",
    "NotificationMarkReadResponse",
    "NotificationSeverity",
]