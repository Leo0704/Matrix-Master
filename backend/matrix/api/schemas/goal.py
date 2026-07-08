"""Pydantic schemas — goals。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

GoalStatus = Literal["active", "achieved", "failed", "cancelled"]

# 统一的 Goal.type 字面值。
# 历史来源：
# - chat 路由关键词分类：publish_note / interact / collect_metrics / warmup / login / generic
# - 前端 goal-form：natural_language
# - RunManager.create_run 默认：publish（已废弃，改用 publish_note）
GoalType = Literal[
    "publish_note",     # 发笔记（chat 默认）
    "interact",         # 互动（评论/点赞/关注）
    "collect_metrics",  # 数据回采
    "warmup",           # 养号
    "login",            # 登录
    "natural_language", # 前端 goal-form 提交的自然语言目标（agent 会自己解析）
    "generic",          # 兜底
]


class ThemeTarget(BaseModel):
    """结构化主题对象：chat LLM 对话收敛出的"主题 + 人群 + 商品类目"。

    所有字段都可选 —— LLM 一次性输出可能只覆盖部分；前端按字段缺失降级展示。
    任意额外字段都允许透传（extra='allow'），保证 LLM 创造性输出不丢信息。
    """

    model_config = ConfigDict(extra="allow")

    theme: Optional[str] = None  # 例：'平价百搭女鞋带货'
    audience: Optional[str] = None  # 例：'大学生'
    product_category: Optional[str] = None  # 例：'鞋子'
    persona_id: Optional[uuid.UUID] = None
    goal_type: Optional[str] = None  # 派生：publish_note / interact / collect_metrics / warmup / login
    extra: dict[str, Any] = Field(default_factory=dict)


# target 接受 ThemeTarget 结构化对象 或 任意 dict（向后兼容旧调用方）
GoalTarget = Union[ThemeTarget, dict[str, Any]]


class Goal(BaseModel):
    id: uuid.UUID
    type: GoalType
    target: dict[str, Any] = Field(default_factory=dict)
    deadline: Optional[datetime] = None
    status: GoalStatus = "active"


class GoalCreate(BaseModel):
    type: GoalType
    target: dict[str, Any] = Field(default_factory=dict)
    deadline: Optional[datetime] = None


class GoalListResponse(BaseModel):
    items: list[Goal]


__all__ = [
    "Goal",
    "GoalCreate",
    "GoalListResponse",
    "GoalStatus",
    "GoalType",
    "ThemeTarget",
    "GoalTarget",
]
