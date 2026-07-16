"""Pydantic schemas — analytics (按日聚合序列 + 账号内容表现 + 多业务对比)。"""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class TaskThroughputPoint(BaseModel):
    date: str  # YYYY-MM-DD
    success: int = 0
    failed: int = 0


class TaskThroughputResponse(BaseModel):
    items: list[TaskThroughputPoint] = Field(default_factory=list)
    days: int


class AccountRiskBucket(BaseModel):
    range: str
    count: int


class AccountRiskResponse(BaseModel):
    items: list[AccountRiskBucket] = Field(default_factory=list)
    total: int = 0


class LlmCostPoint(BaseModel):
    """按日 LLM 成本数据点。"""

    date: str  # YYYY-MM-DD
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


class LlmCostResponse(BaseModel):
    items: list[LlmCostPoint] = Field(default_factory=list)
    days: int = 0
    total_cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# 账号内容表现（v0.7 数据看板核心指标）
# ---------------------------------------------------------------------------


class AccountContentStats(BaseModel):
    """单个账号的内容表现聚合（看板一张卡片一条）。

    字段语义对齐老板选的「内容表现」指标：
      - ``total_notes``：所有状态（draft/scheduled/published/failed...）笔记总数
      - ``published``：已发布数
      - ``draft``：草稿数（含 DRAFT 节点落库还没绑账号的草稿——这种 account_id=NULL，会单独统计）
      - ``scheduled``：已排期未发数
      - ``avg_views`` / ``avg_likes`` / ``avg_comments``：已发布笔记的**最新累计**平均值
        （note_metrics 时序表取 max(ts) 那行）
    """

    account_id: Optional[str] = None  # NULL = 草稿池（未分配账号的草稿）
    handle: str  # 账号昵称；草稿池显示 "(未分配草稿)"
    status: str  # accounts.status：pending/active/offline/...
    # 关联设备昵称（严格一机一账号下，每个账号对应一台设备）
    device_nickname: Optional[str] = None
    total_notes: int = 0
    published: int = 0
    draft: int = 0
    scheduled: int = 0
    avg_views: float = 0.0
    avg_likes: float = 0.0
    avg_comments: float = 0.0


class AccountContentStatsResponse(BaseModel):
    items: list[AccountContentStats] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# v0.7+ 多业务对比（dashboard 第 4 期）
# ---------------------------------------------------------------------------


class BusinessComparisonRow(BaseModel):
    """单个业务的对比数据（dashboard 表格一行）。"""

    business_id: uuid.UUID
    business_name: str
    business_slug: str
    status: str  # active / archived
    # 资源计数
    devices: int = 0
    accounts: int = 0
    personas: int = 0
    goals: int = 0
    notes: int = 0
    published_notes: int = 0
    kb_documents: int = 0
    agent_runs: int = 0
    successful_runs: int = 0
    # 衍生
    notes_per_account: float = 0.0  # notes / accounts


class BusinessComparisonResponse(BaseModel):
    items: list[BusinessComparisonRow] = Field(default_factory=list)
    total_businesses: int = 0


__all__ = [
    "TaskThroughputPoint",
    "TaskThroughputResponse",
    "AccountRiskBucket",
    "AccountRiskResponse",
    "LlmCostPoint",
    "LlmCostResponse",
    "AccountContentStats",
    "AccountContentStatsResponse",
    "BusinessComparisonRow",
    "BusinessComparisonResponse",
]
