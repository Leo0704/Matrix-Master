"""/api/v1/metrics/summary — 监控指标汇总。

从 DB 聚合设备 / 账号 / 任务 / LLM 成本统计。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import Health, OkResponse  # noqa: F401  (Health unused 但保持 schema 聚合)
from matrix.db.models import (
    Account,
    AgentRun,
    Device,
    LlmUsage,
    Task,
)
from pydantic import BaseModel

router = APIRouter(prefix="/metrics", tags=["metrics"])


class DevicesBlock(BaseModel):
    total: int = 0
    active: int = 0
    offline: int = 0


class AccountsBlock(BaseModel):
    total: int = 0
    active: int = 0
    high_risk: int = 0


class TasksBlock(BaseModel):
    pending: int = 0
    running: int = 0
    success_24h: int = 0
    failed_24h: int = 0


class MetricsSummary(BaseModel):
    devices: DevicesBlock = DevicesBlock()
    accounts: AccountsBlock = AccountsBlock()
    tasks: TasksBlock = TasksBlock()
    llm_cost_24h_usd: float = 0.0


def _ago(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


@router.get("/summary", response_model=MetricsSummary)
async def get_metrics_summary(
    session: AsyncSession = Depends(get_db),
) -> MetricsSummary:
    # --- devices ---
    dev_total = (
        await session.execute(
            select(func.count(Device.id)).where(Device.deleted_at.is_(None))
        )
    ).scalar_one()
    dev_active = (
        await session.execute(
            select(func.count(Device.id)).where(
                Device.deleted_at.is_(None), Device.status == "active"
            )
        )
    ).scalar_one()
    dev_offline = (
        await session.execute(
            select(func.count(Device.id)).where(
                Device.deleted_at.is_(None), Device.status == "offline"
            )
        )
    ).scalar_one()

    # --- accounts ---
    acc_total = (
        await session.execute(
            select(func.count(Account.id)).where(Account.deleted_at.is_(None))
        )
    ).scalar_one()
    acc_active = (
        await session.execute(
            select(func.count(Account.id)).where(
                Account.deleted_at.is_(None), Account.status == "active"
            )
        )
    ).scalar_one()
    acc_high_risk = (
        await session.execute(
            select(func.count(Account.id)).where(
                Account.deleted_at.is_(None), Account.risk_score > 0.7
            )
        )
    ).scalar_one()

    # --- tasks ---
    t_pending = (
        await session.execute(
            select(func.count(Task.id)).where(Task.status == "pending")
        )
    ).scalar_one()
    t_running = (
        await session.execute(
            select(func.count(Task.id)).where(Task.status == "running")
        )
    ).scalar_one()
    t_success_24h = (
        await session.execute(
            select(func.count(Task.id)).where(
                Task.status == "success",
                Task.executed_at >= _ago(24),
            )
        )
    ).scalar_one()
    t_failed_24h = (
        await session.execute(
            select(func.count(Task.id)).where(
                Task.status == "failed",
                Task.executed_at >= _ago(24),
            )
        )
    ).scalar_one()

    # --- llm cost 24h ---
    cost = (
        await session.execute(
            select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(
                LlmUsage.ts >= _ago(24)
            )
        )
    ).scalar_one()

    return MetricsSummary(
        devices=DevicesBlock(
            total=int(dev_total), active=int(dev_active), offline=int(dev_offline)
        ),
        accounts=AccountsBlock(
            total=int(acc_total),
            active=int(acc_active),
            high_risk=int(acc_high_risk),
        ),
        tasks=TasksBlock(
            pending=int(t_pending),
            running=int(t_running),
            success_24h=int(t_success_24h),
            failed_24h=int(t_failed_24h),
        ),
        llm_cost_24h_usd=float(cost or 0.0),
    )
