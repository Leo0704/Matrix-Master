"""FastAPI middleware：请求日志上下文注入。

提供：
- ``MonitoringMiddleware``：每个请求把 method/path/trace_id 绑定到 structlog
  context（让一次请求内的日志可关联），并把 trace_id 写入 response header
  （便于客户端做关联）
- 不依赖 ``matrix.api``（避免循环依赖）
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from matrix.monitoring.logging import bind_context, clear_context

# 用模板替换高基数 path（UUID 等），避免日志里的 path/action 字段被 UUID 刷屏
_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_PATH_TEMPLATE_RE = re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_PATH_NORMALIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(rf"^/api/v1/devices/{_UUID_RE}(?P<rest>.*)$", re.I), "/api/v1/devices/{{device_id}}{rest}"),
    (re.compile(rf"^/api/v1/accounts/{_UUID_RE}(?P<rest>.*)$", re.I), "/api/v1/accounts/{{account_id}}{rest}"),
    (re.compile(rf"^/api/v1/personas/{_UUID_RE}(?P<rest>.*)$", re.I), "/api/v1/personas/{{persona_id}}{rest}"),
    (re.compile(rf"^/api/v1/notes/{_UUID_RE}(?P<rest>.*)$", re.I), "/api/v1/notes/{{note_id}}{rest}"),
    (re.compile(rf"^/api/v1/goals/{_UUID_RE}(?P<rest>.*)$", re.I), "/api/v1/goals/{{goal_id}}{rest}"),
    (re.compile(rf"^/api/v1/agent-runs/{_UUID_RE}(?P<rest>.*)$", re.I), "/api/v1/agent-runs/{{run_id}}{rest}"),
    (re.compile(rf"^/api/v1/interactions/{_UUID_RE}(?P<rest>.*)$", re.I), "/api/v1/interactions/{{interaction_id}}{rest}"),
    (re.compile(rf"^/api/v1/kb/documents/{_UUID_RE}(?P<rest>.*)$", re.I), "/api/v1/kb/documents/{{doc_id}}{rest}"),
)


def _normalize_path(path: str) -> str:
    """按资源类型归一化 UUID 段；无法匹配的折叠成 ``{id}``。"""
    for pattern, template in _PATH_NORMALIZERS:
        m = pattern.match(path)
        if m:
            return template.format(**m.groupdict())
    return _PATH_TEMPLATE_RE.sub("/{id}", path)


def _normalize_trace_id(raw: str | None) -> str:
    """校验 X-Request-ID header；只接受 32 字符 hex（小写），其他返回空串。"""
    if not raw:
        return ""
    candidate = raw.strip().lower()
    if len(candidate) != 32:
        return ""
    if not all(c in "0123456789abcdef" for c in candidate):
        return ""
    return candidate


class MonitoringMiddleware(BaseHTTPMiddleware):
    """把每个请求的 method / path / trace_id 绑定到 structlog context。

    trace_id 取自 ``X-Request-ID`` header（Web frontend / CLI 工具注入）；
    客户端在调用前生成 32 位 hex（如 ``crypto.randomUUID()`` 转 32 hex）发此 header，
    让同一次用户动作的日志能串联同一个 trace_id。
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        trace_id_hex = _normalize_trace_id(request.headers.get("x-request-id"))

        method = request.method
        path = _normalize_path(request.url.path)

        bind_context(
            method=method,
            path=path,
            trace_id=trace_id_hex,
            action=f"{method} {path}",
        )

        # 把 trace_id 写到 request.state，让 unhandled exception handler 也能取到
        # （handler 跑在 response 出来之前，看不到 response header）
        if trace_id_hex:
            request.state.trace_id = trace_id_hex

        try:
            response = await call_next(request)
            if trace_id_hex:
                response.headers["X-Trace-Id"] = trace_id_hex
            return response
        finally:
            clear_context()


def install_middleware(app: FastAPI) -> None:
    """把 MonitoringMiddleware 安装到 FastAPI app。"""
    app.add_middleware(MonitoringMiddleware)
