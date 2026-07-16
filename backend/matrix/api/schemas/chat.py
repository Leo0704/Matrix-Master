"""Pydantic schemas — chat (运营小助手)。

v0.7+：chat 从"建目标入口"重定位为"运营小助手"，支持 5 类高频场景：
  - ask_data: 问数据（只读）
  - diagnose: 诊断（只读 + 二次 LLM 归因）
  - preview_change / apply_change: 调参数（带预览 + 确认令牌）
  - browse_kb: 审 KB 经验卡（只读）

不再支持建目标（建目标走 POST /goals 手动表单）。
"""
from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# 13 类 action.type —— 前端按此 dispatch 渲染分支
ChatActionType = Literal[
    # === 正常场景（5 + 1 闲聊）===
    "ask_data",
    "diagnose",
    "preview_change",
    "apply_change",
    "browse_kb",
    "chitchat",
    # === 控制类 ===
    "noop",
    # === 错误兜底（5 类）===
    "llm_error",
    "parse_error",
    "unknown_intent",
    "missing_args",
    "batch_too_large",
    "partial_success",
]


class ChatHistoryMessage(BaseModel):
    """单条历史消息。前端 localStorage 自管对话历史，每次发送全量 POST 给后端。"""

    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatHistoryMessage] = Field(default_factory=list)
    session_id: Optional[uuid.UUID] = None  # 保留字段；当前后端无状态


class ChatAction(BaseModel):
    """触发的操作（前端按 type 分支渲染）。

    ``payload`` 内部结构按 type 自治（保持 dict 逃生舱形状），
    前端用 typeof 鉴别。``needs_confirmation=True`` 时前端必须显示
    确认/取消按钮；``confirmation_token`` 是第二次 POST 的幂等键。
    """

    type: ChatActionType
    payload: dict[str, Any] = Field(default_factory=dict)
    needs_confirmation: bool = False
    confirmation_token: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    action: Optional[ChatAction] = None
    confirmation_token: Optional[str] = None  # 透传 ChatAction.confirmation_token
    error_hint: Optional[str] = None  # 错误类的可读补充，UI 直接展示


__all__ = [
    "ChatActionType",
    "ChatHistoryMessage",
    "ChatRequest",
    "ChatAction",
    "ChatResponse",
]