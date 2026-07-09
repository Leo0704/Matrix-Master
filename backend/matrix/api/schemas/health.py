"""Pydantic schemas — health / common / error envelope。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from matrix import __version__


class Health(BaseModel):
    """GET /api/v1/health 响应。"""

    status: Literal["ok", "degraded", "down"] = "ok"
    version: str = Field(default_factory=lambda: __version__)
    uptime_sec: int = 0
    db: Literal["ok", "error"] = "ok"
    tailscale: Literal["connected", "disconnected"] = "disconnected"


class OkResponse(BaseModel):
    """统一 ok 响应（cancel 等只回执的端点用）。"""

    ok: bool = True


class ErrorDetail(BaseModel):
    code: Literal[
        "DEVICE_OFFLINE",
        "APP_NOT_FOUND",
        "SELECTOR_NOT_FOUND",
        "TIMEOUT",
        "IME_ERROR",
        "DRAFT_FAILED",
        "UPLOAD_FAILED",
        "RISK_BLOCKED",
        "RATE_LIMITED",
        "PARSE_FAILED",
        "INVALID_PARAMS",
        "VALIDATION_ERROR",
        "UNAUTHORIZED",
        "FORBIDDEN",
        "NOT_FOUND",
        "CONFLICT",
        "INTERNAL_ERROR",
        "UNKNOWN",
    ]
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    ok: Literal[False] = False
    error: ErrorDetail
