"""Pydantic schemas — chat (自然语言指令)。"""
from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    """单条历史消息。前端 localStorage 自管对话历史，每次发送全量 POST 给后端。"""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryMessage] = Field(default_factory=list)
    session_id: Optional[uuid.UUID] = None  # 保留字段；当前后端无状态


class ChatAction(BaseModel):
    """触发的操作（创建 goal / 暂停 / 接管等）。"""

    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    reply: str
    # 主题识别结果：LLM 在多轮对话收敛完毕那一刻输出 theme_confirmed=true，
    # 后端据此建 Goal + AgentRun。theme_payload 是结构化主题对象。
    theme_confirmed: bool = False
    theme_payload: Optional[dict[str, Any]] = None
    action: Optional[ChatAction] = None


__all__ = [
    "ChatRequest",
    "ChatAction",
    "ChatResponse",
    "ChatHistoryMessage",
]
