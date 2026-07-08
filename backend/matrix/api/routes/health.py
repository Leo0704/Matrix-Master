"""GET /api/v1/health — 健康检查。"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from matrix.api.schemas import Health

router = APIRouter(tags=["health"])

_START_TIME = time.monotonic()


@router.get("/health", response_model=Health)
async def get_health(request: Request) -> Health:
    """健康检查。返回版本、uptime、DB 状态、Tailscale 状态。"""
    db_status: str = "ok"
    try:
        factory = request.app.state.db_session_factory
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - 真实部署才有 DB
        db_status = "error"

    return Health(
        status="ok" if db_status == "ok" else "degraded",
        uptime_sec=int(time.monotonic() - _START_TIME),
        db=db_status,  # type: ignore[arg-type]
        tailscale="disconnected",  # TODO: 真实检查
    )
