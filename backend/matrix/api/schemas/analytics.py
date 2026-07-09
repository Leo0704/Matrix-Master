"""Pydantic schemas — analytics (按日聚合序列 + 风险分布)。"""
from __future__ import annotations

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


__all__ = [
    "TaskThroughputPoint",
    "TaskThroughputResponse",
    "AccountRiskBucket",
    "AccountRiskResponse",
    "LlmCostPoint",
    "LlmCostResponse",
]
