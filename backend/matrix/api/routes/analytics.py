"""分析 / 图表端点：按日聚合的序列 + 账号风险分布。

- GET /analytics/task-throughput?days=14   tasks 表按日 success/failed
- GET /analytics/llm-cost?days=14         llm_usage 表按日 cost_usd 之和
- GET /analytics/account-risk             accounts.risk_score 区间分布
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    LlmCostPoint,
    LlmCostResponse,
    TaskThroughputPoint,
    TaskThroughputResponse,
)
from matrix.db.models import Account, LlmUsage, Task
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _fill_date_range(
    end: datetime, days: int, items: dict[str, dict]
) -> list[tuple[str, dict]]:
    """把稀疏聚合补齐到连续 N 天。"""
    out: list[tuple[str, dict]] = []
    for i in range(days - 1, -1, -1):
        d = (end - timedelta(days=i)).date().isoformat()
        out.append((d, items.get(d, {})))
    return out


@router.get("/task-throughput", response_model=TaskThroughputResponse)
async def task_throughput(
    days: int = Query(14, ge=1, le=90),
    session: AsyncSession = Depends(get_db),
) -> TaskThroughputResponse:
    """按日聚合 tasks 表的 success / failed 计数。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    ts_col = func.coalesce(Task.executed_at, Task.created_at)

    stmt = (
        select(
            func.date(ts_col).label("day"),
            Task.status,
            func.count(Task.id).label("n"),
        )
        .where(ts_col >= start, Task.status.in_(("success", "failed")))
        .group_by(func.date(ts_col), Task.status)
    )
    rows = (await session.execute(stmt)).all()
    by_day: dict[str, dict[str, int]] = {}
    for day, status, n in rows:
        d = str(day)
        by_day.setdefault(d, {"success": 0, "failed": 0})[status] = int(n)

    points = [
        TaskThroughputPoint(date=d, **vals)
        for d, vals in _fill_date_range(end, days, by_day)
    ]
    return TaskThroughputResponse(items=points, days=days)


@router.get("/llm-cost", response_model=LlmCostResponse)
async def llm_cost(
    days: int = Query(14, ge=1, le=90),
    session: AsyncSession = Depends(get_db),
) -> LlmCostResponse:
    """按日聚合 llm_usage.cost_usd 之和。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    stmt = select(
        func.date(LlmUsage.ts).label("day"),
        func.coalesce(func.sum(LlmUsage.cost_usd), 0.0).label("cost"),
    ).where(LlmUsage.ts >= start).group_by(func.date(LlmUsage.ts))
    rows = (await session.execute(stmt)).all()
    by_day = {str(day): {"cost": float(cost)} for day, cost in rows}

    points = [
        LlmCostPoint(date=d, cost=vals.get("cost", 0.0))
        for d, vals in _fill_date_range(end, days, by_day)
    ]
    return LlmCostResponse(items=points, days=days)


# ---------------------------------------------------------------------------
# 账号风险分布
# ---------------------------------------------------------------------------


class AccountRiskBucket(BaseModel):
    range: str  # "0-0.2" / "0.2-0.4" / ...
    count: int


class AccountRiskResponse(BaseModel):
    items: list[AccountRiskBucket]
    total: int


_BUCKETS = [
    ("0-0.2", 0.0, 0.2),
    ("0.2-0.4", 0.2, 0.4),
    ("0.4-0.6", 0.4, 0.6),
    ("0.6-0.8", 0.6, 0.8),
    ("0.8-1.0", 0.8, 1.0001),
]


@router.get("/account-risk", response_model=AccountRiskResponse)
async def account_risk(
    session: AsyncSession = Depends(get_db),
) -> AccountRiskResponse:
    """按 5 个风险区间统计账号数（含 deleted_at IS NULL）。"""
    rows = (
        await session.execute(
            select(Account.risk_score).where(Account.deleted_at.is_(None))
        )
    ).scalars().all()
    counts = {label: 0 for label, _, _ in _BUCKETS}
    for score in rows:
        s = float(score or 0)
        for label, lo, hi in _BUCKETS:
            if lo <= s < hi:
                counts[label] += 1
                break
    items = [AccountRiskBucket(range=label, count=counts[label]) for label, _, _ in _BUCKETS]
    return AccountRiskResponse(items=items, total=sum(counts.values()))
