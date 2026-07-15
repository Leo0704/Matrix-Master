"""Phase 1 反向反馈通知器：写 notifications 表 + 可选 POST webhook。

替换 ``bootstrap._noop_notifier``，注入到 ``AgentServices.notifier``。

设计要点：
- 满足 ``protocols.Notifier`` 协议：``async def __call__(code, payload) -> None``
- **DB 写在前**：哪怕 webhook 挂了，运营人也能从 ``/notifications`` 看到事件
- **post-commit 才发 webhook**（参考 ``monitoring/alert_scanner.py:194-212``）：
  webhook 失败不影响 DB 已落库的事实
- **永不抛异常**：notifier 是旁路，挂了就只记日志，不挡主流程
- httpx 客户端长生命周期，在 lifespan 收尾调 ``aclose()`` 关掉
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Optional
from uuid import UUID

import httpx

from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 通知 code 元数据：severity + title/body 模板
# ---------------------------------------------------------------------------

DEFAULT_RECIPIENT = "operator"


@dataclass(frozen=True)
class _NotifyMeta:
    severity: str  # 'info' | 'success' | 'warning' | 'error'
    title: str  # 支持 {key} 占位（str.format）
    body: str


_NOTIFY_META: dict[str, _NotifyMeta] = {
    # orchestrator 阶段通知（Step 5）
    "goal.round.prepared": _NotifyMeta(
        severity="info",
        title="第 {round_number} 轮：已派出 {runs_created} 条",
        body="下一阶段 EXECUTING。预计 {eta_min} 分钟内开始回数据。无需操作。",
    ),
    "goal.round.monitored": _NotifyMeta(
        severity="info",
        title="第 {round_number} 轮：{notes_count} 条笔记已观察",
        body="数据已写入 note_metrics。下一阶段 SUMMARIZING。无需操作。",
    ),
    "goal.round.decided": _NotifyMeta(
        severity="info",
        title="第 {round_number} 轮：已总结完",
        body="下一阶段 DECIDING，将判断是否继续下一轮。无需操作。",
    ),
    "goal.round.decided.continue": _NotifyMeta(
        severity="info",
        title="第 {round_number} 轮：{reason}",
        body="即将开始第 {next_round} 轮 PREPARING。预计 {eta_min} 分钟。无需操作。",
    ),
    "goal.round.decided.done": _NotifyMeta(
        severity="success",
        title="目标完成：{reason}",
        body="共 {total_rounds} 轮。可在 /goals/{goal_id} 查看总结。",
    ),
    # publish_node 通知（Step 6）
    "note.published": _NotifyMeta(
        severity="success",
        title="笔记已发布：{title}",
        body="24 小时后将自动采集表现数据。在 /notes/{note_id} 跟踪。",
    ),
    # _do_collect 通知（Step 7）
    "note.collected": _NotifyMeta(
        severity="success",
        title="数据采集完成 ({short_id})",
        body=(
            "views {views} / likes {likes} / collects {collects} / "
            "comments {comments} / follows_gained {follows_gained}。"
            "已写入 note_metrics。"
        ),
    ),
    "note.collect.failed": _NotifyMeta(
        severity="warning",
        title="采集失败 ({short_id})",
        body="设备 {device_id} 离线或平台拒绝。调度器将自动重试。",
    ),
    # 现有 Alert 节点（保持兼容）
    "agent.alert": _NotifyMeta(
        severity="warning",
        title="告警：{code}",
        body="run {run_id}：{message}",
    ),
}

_DEFAULT_META = _NotifyMeta(
    severity="info", title="{code}", body="{payload}"
)


# ---------------------------------------------------------------------------
# WebhookNotifier
# ---------------------------------------------------------------------------

# 避免循环依赖：运行时 import ORM/session。Type hints 用字符串。
ConfigReaderFn = Callable[[str, Any], Awaitable[Any]]


class WebhookNotifier:
    """Phase 1 notifier：DB 写 notifications + 可选 POST webhook。"""

    def __init__(
        self,
        *,
        session_factory: Any,
        config_reader: ConfigReaderFn,
        http_client: Optional[httpx.AsyncClient] = None,
        recipient: str = DEFAULT_RECIPIENT,
        webhook_url_key: str = "matrix.notifications.webhook_url",
        max_retries: int = 3,
    ) -> None:
        self._factory = session_factory
        self._config_reader = config_reader
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=5.0)
        self._recipient = recipient
        self._webhook_url_key = webhook_url_key
        self._max_retries = max_retries

    async def __call__(self, code: str, payload: dict[str, Any]) -> None:
        """Notifier 协议实现：永不上抛异常。"""
        try:
            await self._dispatch(code, payload)
        except Exception:  # noqa: BLE001
            logger.exception("notifier.call failed", code=code)

    async def _dispatch(self, code: str, payload: dict[str, Any]) -> None:
        meta = _NOTIFY_META.get(code, _DEFAULT_META)
        try:
            title = meta.title.format(**_safe_format_map(payload))
            body = meta.body.format(**_safe_format_map(payload))
        except (KeyError, IndexError):
            # payload 缺字段时退到默认模板，避免格式化异常把通知整没了
            title = meta.title
            body = meta.body

        # 1) 写 DB —— 哪怕 webhook 挂，运营也能从 /notifications 看到事件
        await self._write_db(code, payload, meta.severity, title, body)

        # 2) 可选 webhook
        url = await self._config_reader(self._webhook_url_key, None)
        if url:
            await self._post_webhook(str(url), code, payload)

    async def _write_db(
        self,
        code: str,
        payload: dict[str, Any],
        severity: str,
        title: str,
        body: str,
    ) -> None:
        # 运行时 import 避免循环依赖
        from matrix.db.models import Notification
        from sqlalchemy import insert

        async with self._factory() as session:
            try:
                stmt = insert(Notification).values(
                    recipient=self._recipient,
                    code=code,
                    severity=severity,
                    title=title[:256],  # 列宽 256，截断防超长
                    body=body,
                    goal_id=_opt_uuid(payload.get("goal_id")),
                    run_id=_opt_uuid(payload.get("run_id")),
                    note_id=_opt_uuid(payload.get("note_id")),
                    device_id=_opt_uuid(payload.get("device_id")),
                    payload=payload,
                )
                await session.execute(stmt)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def _post_webhook(self, url: str, code: str, payload: dict[str, Any]) -> None:
        body = {"code": code, "payload": payload}
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post(url, json=body, timeout=5.0)
                if 200 <= resp.status_code < 300:
                    return
                if 400 <= resp.status_code < 500:
                    # 4xx 不重试（配置错误），记日志返回
                    logger.warning(
                        "notifier.webhook_4xx",
                        url=url,
                        status=resp.status_code,
                        code=code,
                    )
                    return
                # 5xx 走重试
                logger.warning(
                    "notifier.webhook_5xx_retry",
                    url=url,
                    status=resp.status_code,
                    attempt=attempt,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning(
                    "notifier.webhook_transport_error",
                    url=url,
                    attempt=attempt,
                    err=str(exc),
                )
            if attempt < self._max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _opt_uuid(value: Any) -> Optional[UUID]:
    """把 dict 里可能为 None / 字符串 / UUID 的字段转成 UUID 或 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _safe_format_map(payload: dict[str, Any]) -> dict[str, Any]:
    """给 ``str.format`` 兜底：缺字段时给个空串，避免 KeyError。"""
    sentinel = object()
    return {
        k: (v if v is not None else "") if isinstance(v, (str, int, float)) else str(v)
        for k, v in payload.items()
    } if payload else {}


__all__ = ["WebhookNotifier", "DEFAULT_RECIPIENT", "ConfigReaderFn"]