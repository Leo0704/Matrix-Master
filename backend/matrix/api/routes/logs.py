"""POST /api/v1/logs — APK 日志 ingest。

APK（Android Companion）通过 Ktor HttpClient 把日志 batch POST 到这里，
每条日志被解析成 structlog 事件并走现有的 JSON 输出链路。

约束（与网络假设一致，见 docs/operations/log-schema.md §3.3）：
- 不强制 HMAC；APK 通过 Tailscale + adb-reverse localhost 调本机 master，
  网络层隔离保证访问控制
- 批量上限：单次 200 条（防止大 payload）
- 字段透传：APK 不强制 schema 字段（attrs 是自由 dict），master 这边只补
  service/version，并把每条日志直接喂给 structlog logger —— 字段填充率由
  现有 ``LOG_FIELDS`` 白名单 + ``_normalize_record`` 处理器统一兜底
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from matrix.monitoring.logging import bind_context, clear_context, get_logger

router = APIRouter(prefix="/logs", tags=["logs"])

_LOG = get_logger(__name__)

# 单次请求最多 200 条，避免单请求太大把 master 阻塞
_MAX_BATCH = 200


class LogLine(BaseModel):
    """APK 上行的一条日志。

    字段尽量宽松：level / event / message / attrs 都允许缺省。
    """

    ts: str | None = None
    level: str = "info"
    event: str | None = None
    message: str | None = None
    attrs: dict[str, Any] = Field(default_factory=dict)
    throwable: str | None = None


class LogsBatch(BaseModel):
    """APK 一个周期（默认 30s）攒的批量日志。"""

    device_id: str | None = None
    app_version: str | None = None
    trace_id: str | None = None
    lines: list[LogLine] = Field(default_factory=list)


def _bind_optional(**kwargs: Any) -> None:
    """bind_contextvars 只在值非空时调，避免无意义空串字段污染日志。"""
    nonempty = {k: v for k, v in kwargs.items() if v}
    if nonempty:
        bind_context(**nonempty)


@router.post("", status_code=202)
async def ingest_logs(batch: LogsBatch) -> dict[str, int]:
    """接收 APK 日志；逐条 structlog 输出，写到 ~/.matrix/logs/*.jsonl。

    返回 received 数量（HTTP 202 表示已接收处理，不保证落盘）。
    """
    if not batch.lines:
        return {"received": 0}

    if len(batch.lines) > _MAX_BATCH:
        # 不直接拒绝——截断前 N 条避免阻塞，但记一笔警告
        _LOG.warning(
            "logs.ingest.batch_truncated",
            received=len(batch.lines),
            max=_MAX_BATCH,
        )
        batch.lines = batch.lines[:_MAX_BATCH]

    _bind_optional(
        source="matrix-apk",
        device_id=batch.device_id,
        app_version=batch.app_version,
        trace_id=batch.trace_id,
    )

    try:
        for line in batch.lines:
            level = (line.level or "info").lower()
            message = line.message or line.event or "(no message)"

            # structlog BoundLogger.<level>(event, **kwargs)
            logger_obj = get_logger("matrix.apk")
            method = getattr(logger_obj, level, logger_obj.info)

            # 优先把 event 名当事件，message 作 fallback
            event_name = line.event or "apk.log"
            extra: dict[str, Any] = dict(line.attrs)
            if line.ts:
                extra["apk_ts"] = line.ts
            if line.throwable:
                extra["throwable"] = line.throwable

            # 用 kwargs 风格调用——structlog 处理
            # 注意：当 level=error 时，用 error() 而不是 exception()，
            # 因为 exception 需要当前在 except 块里
            if level == "error":
                method(event_name, message=message, **extra)
            else:
                method(event_name, message=message, **extra)

        return {"received": len(batch.lines)}
    except Exception as exc:  # pragma: no cover
        _LOG.exception("logs.ingest.failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}")
    finally:
        clear_context()
