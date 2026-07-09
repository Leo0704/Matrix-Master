"""告警端点：把 monitoring/alerts.py 9 条 check 规则的扫描结果入库 + 查询 / 处理。

GET    /alerts?resolved=&code=&severity=   列出告警
POST   /alerts/scan                       手动触发一次扫描（写入 alerts 表）
POST   /alerts/{id}/resolve               标记已处理
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    AlertItem,
    AlertListResponse,
    AlertResolveRequest,
    AlertResolveResponse,
)
from matrix.db.models import Alert as AlertORM
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _to_schema(a: AlertORM) -> AlertItem:
    return AlertItem(
        id=a.id,
        code=a.code,
        severity=a.severity,  # type: ignore[arg-type]
        message=a.message,
        subject_id=a.subject_id,
        resolved=a.resolved,
        created_at=a.created_at,
        resolved_at=a.resolved_at,
    )


@router.get("", response_model=AlertListResponse)
async def list_alerts(
    resolved: Optional[bool] = Query(None),
    code: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> AlertListResponse:
    stmt = select(AlertORM)
    count_stmt = select(func.count(AlertORM.id))
    if resolved is not None:
        stmt = stmt.where(AlertORM.resolved == resolved)
        count_stmt = count_stmt.where(AlertORM.resolved == resolved)
    if code:
        stmt = stmt.where(AlertORM.code == code)
        count_stmt = count_stmt.where(AlertORM.code == code)
    if severity:
        stmt = stmt.where(AlertORM.severity == severity)
        count_stmt = count_stmt.where(AlertORM.severity == severity)

    stmt = stmt.order_by(AlertORM.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    return AlertListResponse(items=[_to_schema(r) for r in rows], total=total)


@router.post("/scan", response_model=AlertListResponse)
async def scan_alerts(
    session: AsyncSession = Depends(get_db),
) -> AlertListResponse:
    """手动触发扫描：跑 9 条 check 规则，把新触发的告警写库。

    已有 (code, subject_id, resolved=False) 的告警不再重复写（幂等）。
    """
    from sqlalchemy import and_, exists
    from matrix.monitoring.alerts import (
        check_budget_exceeded,
        check_device_offline,
        check_risk_blocked,
        check_selector_not_found,
        check_tailscale_derp_lost,
    )

    # 收集现有 (code, subject_id) — resolved=False 的告警，避免重复
    existing_stmt = select(AlertORM.code, AlertORM.subject_id).where(
        AlertORM.resolved == False  # noqa: E712
    )
    existing = {(r[0], r[1]) for r in (await session.execute(existing_stmt)).all()}

    new_alerts = []

    # 1. 设备离线（简单示例：从 devices 表查 last_heartbeat）
    from matrix.db.models import Device

    devs = (
        await session.execute(
            select(Device).where(Device.deleted_at.is_(None), Device.status == "active")
        )
    ).scalars().all()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    device_payloads = []
    for d in devs:
        age = 0.0
        if d.last_heartbeat:
            try:
                age = max(0.0, (now - d.last_heartbeat).total_seconds())
            except Exception:
                age = 0.0
        device_payloads.append(
            {"device_id": str(d.id), "last_heartbeat_age_sec": age}
        )
    for a in check_device_offline(device_payloads, heartbeat_threshold_sec=300):
        if (a.code, a.subject_id) not in existing:
            new_alerts.append(a)

    # 2. 账号高风险
    from matrix.db.models import Account

    accts = (
        await session.execute(
            select(Account).where(Account.deleted_at.is_(None))
        )
    ).scalars().all()
    acct_payloads = [
        {"account_id": str(a.id), "risk_score": float(a.risk_score or 0)}
        for a in accts
    ]
    for a in check_risk_blocked(acct_payloads, risk_threshold=0.7):
        if (a.code, a.subject_id) not in existing:
            new_alerts.append(a)

    # 3. 选择器失败 — 暂无事件流，留空
    # 4. Tailscale — 暂无 derp 数据，留空
    # 5. BUDGET_EXCEEDED — 由 LLM 成本聚合判断（需要 daily_budget 上下文）

    written: list[AlertItem] = []
    for a in new_alerts:
        row = AlertORM(
            code=a.code,
            severity=a.severity,
            message=a.message,
            subject_id=a.subject_id,
            resolved=False,
        )
        session.add(row)
        await session.flush()
        written.append(_to_schema(row))
        logger.info("alerts.scan.wrote", code=a.code, subject_id=a.subject_id)

    return AlertListResponse(items=written, total=len(written))


@router.post("/{alert_id}/resolve", response_model=AlertResolveResponse)
async def resolve_alert(
    alert_id: uuid.UUID,
    body: AlertResolveRequest,
    session: AsyncSession = Depends(get_db),
) -> AlertResolveResponse:
    a = await session.get(AlertORM, alert_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    if a.resolved:
        return AlertResolveResponse(id=a.id, resolved=True)  # 幂等
    a.resolved = True
    a.resolved_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info(
        "alerts.resolve", alert_id=alert_id, resolver=body.resolver
    )
    return AlertResolveResponse(id=a.id, resolved=True)
