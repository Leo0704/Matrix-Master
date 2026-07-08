"""FastAPI middleware：HTTP 请求级指标 + trace context 注入。

提供：
- ``MonitoringMiddleware``：每个请求记录 method/path/status/latency_ms，
  并把 trace_id 写入 response header（便于客户端做关联）
- 不依赖 ``matrix.api``（避免循环依赖）
"""

from __future__ import annotations

import re
import time
from typing import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from opentelemetry import trace as otel_trace
from starlette.middleware.base import BaseHTTPMiddleware

from matrix.monitoring.logging import bind_context, clear_context
from matrix.monitoring.metrics import http_request_latency_seconds, http_requests_total

# 用模板替换高基数 path（UUID 等），避免 Prometheus label 爆炸
_PATH_TEMPLATE_RE = re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _normalize_path(path: str) -> str:
    """把动态段（UUID 等）折叠成 ``{id}``，避免高基数 label。"""
    return _PATH_TEMPLATE_RE.sub("/{id}", path)


class MonitoringMiddleware(BaseHTTPMiddleware):
    """记录 HTTP 请求的 method / path / status / latency_ms，并注入 trace context。

    与 metrics 指标联动：
    - ``matrix_http_requests_total{method,path,status}`` Counter
    - ``matrix_http_request_latency_seconds{method,path}`` Histogram
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        start = time.perf_counter()
        # 提取 trace_id 作为日志关联 key
        span = otel_trace.get_current_span()
        ctx = span.get_span_context() if span else None
        trace_id_hex = (
            format(ctx.trace_id, "032x") if ctx and ctx.trace_id else ""
        )

        method = request.method
        path = _normalize_path(request.url.path)

        bind_context(
            method=method,
            path=path,
            trace_id=trace_id_hex,
            action=f"{method} {path}",
        )

        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            if trace_id_hex:
                response.headers["X-Trace-Id"] = trace_id_hex
            return response
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            try:
                http_requests_total.labels(
                    method=method,
                    path=path,
                    status=str(status_code),
                ).inc()
                http_request_latency_seconds.labels(method=method, path=path).observe(
                    elapsed_ms / 1000.0
                )
            except Exception:  # pragma: no cover - 指标失败不应影响请求
                pass
            clear_context()


def install_middleware(app: FastAPI) -> None:
    """把 MonitoringMiddleware 安装到 FastAPI app。"""
    app.add_middleware(MonitoringMiddleware)
