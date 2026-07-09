"""GET /api/v1/health — 健康检查。"""
from __future__ import annotations

import asyncio
import shutil
import time
from typing import Literal

from fastapi import APIRouter, Request
from sqlalchemy import text

from matrix.api.schemas import Health
from matrix.monitoring.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)

_START_TIME = time.monotonic()


TailscaleStatus = Literal["ok", "error", "connected", "disconnected"]


def _check_tailscale_sync(timeout: float = 2.0) -> TailscaleStatus:
    """同步探测 Tailscale 状态。timeout 秒后仍未返回视为 disconnected。

    - tailscale 二进制不在 → disconnected（容器未挂 tailscale 旁路）
    - tailscale status 退出 0 → connected
    - 其它（含超时）→ disconnected
    """
    import subprocess

    if shutil.which("tailscale") is None:
        return "disconnected"
    try:
        proc = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.warning("tailscale probe failed", err=str(e))
        return "disconnected"
    if proc.returncode != 0:
        return "disconnected"
    # BackEndStates 非空即代表真在线（与 LoginServer 握手成功）
    try:
        import json

        data = json.loads(proc.stdout or "{}")
        backend = data.get("BackendState", "")
        return "connected" if backend and backend != "NoState" else "disconnected"
    except (ValueError, TypeError):
        return "connected"


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

    tailscale_status: TailscaleStatus = await asyncio.to_thread(_check_tailscale_sync)

    return Health(
        status="ok" if db_status == "ok" else "degraded",
        uptime_sec=int(time.monotonic() - _START_TIME),
        db=db_status,  # type: ignore[arg-type]
        tailscale=tailscale_status,  # type: ignore[arg-type]
    )
