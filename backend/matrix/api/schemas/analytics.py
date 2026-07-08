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


class LlmCostPoint(BaseModel):
    date: str
    cost: float = 0.0


class LlmCostResponse(BaseModel):
    items: list[LlmCostPoint] = Field(default_factory=list)
    days: int


class AccountRiskBucket(BaseModel):
    range: str
    count: int


class AccountRiskResponse(BaseModel):
    items: list[AccountRiskBucket] = Field(default_factory=list)
    total: int = 0


__all__ = [
    "TaskThroughputPoint",
    "TaskThroughputResponse",
    "LlmCostPoint",
    "LlmCostResponse",
    "AccountRiskBucket",
    "AccountRiskResponse",
]
