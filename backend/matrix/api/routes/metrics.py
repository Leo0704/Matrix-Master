"""/api/v1/metrics/summary — 监控指标汇总。

从 DB 聚合设备 / 账号 / 任务 统计。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.db.models import (
    Account,
    Device,
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


def _ago(hours: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours)


@router.get("/summary", response_model=MetricsSummary)
async def get_metrics_summary(
    business_id: Optional[uuid.UUID] = Query(
        None,
        description=(
            "v0.7+ 业务过滤：设备/账号按自身 business_id 列过滤，"
            "任务经 account_id 关联 accounts.business_id 过滤"
        ),
    ),
    session: AsyncSession = Depends(get_db),
) -> MetricsSummary:
    # --- devices ---
    dev_total_stmt = select(func.count(Device.id)).where(Device.deleted_at.is_(None))
    dev_active_stmt = select(func.count(Device.id)).where(
        Device.deleted_at.is_(None), Device.status == "active"
    )
    dev_offline_stmt = select(func.count(Device.id)).where(
        Device.deleted_at.is_(None), Device.status == "offline"
    )

    # --- accounts ---
    acc_total_stmt = select(func.count(Account.id)).where(Account.deleted_at.is_(None))
    acc_active_stmt = select(func.count(Account.id)).where(
        Account.deleted_at.is_(None), Account.status == "active"
    )
    acc_high_risk_stmt = select(func.count(Account.id)).where(
        Account.deleted_at.is_(None), Account.risk_score > 0.7
    )

    # --- tasks ---
    t_pending_stmt = select(func.count(Task.id)).where(Task.status == "pending")
    t_running_stmt = select(func.count(Task.id)).where(Task.status == "running")
    t_success_stmt = select(func.count(Task.id)).where(
        Task.status == "success",
        Task.executed_at >= _ago(24),
    )
    t_failed_stmt = select(func.count(Task.id)).where(
        Task.status == "failed",
        Task.executed_at >= _ago(24),
    )

    if business_id is not None:
        # v0.7+ 业务过滤（之前参数是摆设）：devices/accounts 直查 business_id 列；
        # tasks 无 business_id 列，经 account_id 关联 accounts 过滤
        dev_total_stmt = dev_total_stmt.where(Device.business_id == business_id)
        dev_active_stmt = dev_active_stmt.where(Device.business_id == business_id)
        dev_offline_stmt = dev_offline_stmt.where(Device.business_id == business_id)
        acc_total_stmt = acc_total_stmt.where(Account.business_id == business_id)
        acc_active_stmt = acc_active_stmt.where(Account.business_id == business_id)
        acc_high_risk_stmt = acc_high_risk_stmt.where(Account.business_id == business_id)
        biz_accounts = select(Account.id).where(Account.business_id == business_id)
        t_pending_stmt = t_pending_stmt.where(Task.account_id.in_(biz_accounts))
        t_running_stmt = t_running_stmt.where(Task.account_id.in_(biz_accounts))
        t_success_stmt = t_success_stmt.where(Task.account_id.in_(biz_accounts))
        t_failed_stmt = t_failed_stmt.where(Task.account_id.in_(biz_accounts))

    dev_total = (await session.execute(dev_total_stmt)).scalar_one()
    dev_active = (await session.execute(dev_active_stmt)).scalar_one()
    dev_offline = (await session.execute(dev_offline_stmt)).scalar_one()
    acc_total = (await session.execute(acc_total_stmt)).scalar_one()
    acc_active = (await session.execute(acc_active_stmt)).scalar_one()
    acc_high_risk = (await session.execute(acc_high_risk_stmt)).scalar_one()
    t_pending = (await session.execute(t_pending_stmt)).scalar_one()
    t_running = (await session.execute(t_running_stmt)).scalar_one()
    t_success_24h = (await session.execute(t_success_stmt)).scalar_one()
    t_failed_24h = (await session.execute(t_failed_stmt)).scalar_one()

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
    )
