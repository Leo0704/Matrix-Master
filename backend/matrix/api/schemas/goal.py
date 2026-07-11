"""Pydantic schemas — goals。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

GoalStatus = Literal["active", "achieved", "failed", "cancelled"]

# Goal-level orchestrator phase（v0.7 第 1 期"中控运营"）
GoalPhase = Literal[
    "PENDING",
    "PREPARING",
    "EXECUTING",
    "MONITORING",
    "SUMMARIZING",
    "DECIDING",
    "DONE",
]

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
    # v0.7 第 1 期：orchestrator 状态机字段
    phase: GoalPhase = "PENDING"
    current_round: int = 1
    max_rounds: int = 3
    # v0.7 第 1 期优化：可调字段（前端创建 goal 时可指定）
    target_likes: int = 500  # KPI 阈值
    notes_per_round: int = 3  # 每轮多少篇
    learning_summary: Optional[str] = None
    phase_updated_at: Optional[datetime] = None


class GoalRound(BaseModel):
    """每轮运营记录（v0.7 第 1 期）。"""

    id: uuid.UUID
    goal_id: uuid.UUID
    round_number: int
    started_at: datetime
    ended_at: Optional[datetime] = None
    kpi_summary: dict[str, Any] = Field(default_factory=dict)
    notes_created: int = 0
    total_views: int = 0
    total_likes: int = 0
    created_at: datetime
    updated_at: datetime


class GoalRoundListResponse(BaseModel):
    items: list[GoalRound]
    total: int = 0


class GoalCreate(BaseModel):
    type: GoalType
    target: dict[str, Any] = Field(default_factory=dict)
    deadline: Optional[datetime] = None
    # 可调字段（不传就用 DB default）
    target_likes: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    notes_per_round: Optional[int] = Field(default=None, ge=1, le=20)
    max_rounds: Optional[int] = Field(default=None, ge=1, le=20)


class GoalUpdate(BaseModel):
    """局部更新 — 所有字段可选，None 表示该字段不动。

    注意：target 是结构化主题对象，更新会**整体覆盖**旧值（不是 merge）。
    支持把 status 改成 'cancelled' 来手动停 goal（v0.7 B）。
    """

    type: Optional[GoalType] = None
    target: Optional[dict[str, Any]] = None
    deadline: Optional[datetime] = None
    target_likes: Optional[int] = Field(default=None, ge=1, le=1_000_000)
    notes_per_round: Optional[int] = Field(default=None, ge=1, le=20)
    max_rounds: Optional[int] = Field(default=None, ge=1, le=20)
    status: Optional["GoalStatus"] = None


class GoalListResponse(BaseModel):
    items: list[Goal]


__all__ = [
    "Goal",
    "GoalCreate",
    "GoalUpdate",
    "GoalListResponse",
    "GoalRound",
    "GoalRoundListResponse",
    "GoalPhase",
    "GoalStatus",
    "GoalType",
    "ThemeTarget",
    "GoalTarget",
]
