"""Phase 1 反向反馈通知器：写 notifications 表 + 可选 POST webhook。

替换 ``bootstrap._noop_notifier``，注入到 ``AgentServices.notifier``。

设计要点：
- 满足 ``protocols.Notifier`` 协议：``async def __call__(code, payload) -> None``
- **DB 写在前**：哪怕 webhook 挂了，运营人也能从 ``/notifications`` 看到事件
- **post-commit 才发 webhook**（参考 ``monitoring/alert_scanner.py:194-212``）：
  webhook 失败不影响 DB 已落库的事实
- **永不抛异常**：notifier 是旁路，挂了就只记日志，不挡主流程
- httpx 客户端长生命周期，在 lifespan 收尾调 ``aclose()`` 关掉

文案约定（v0.7+）：
- 所有发送的 code 必须在 ``_NOTIFY_META`` 注册。未注册直接 logger.error，
  **不发消息**——宁可漏发，也不把 {code}/{payload} 这种程序员内容丢给用户。
- 每条 title/body 必须回答：发生了什么 / 对我意味着什么 / 我该做什么。
- payload 里的 FK（goal_id / run_id / note_id / device_id / account_id）会尽量透传，
  前端据此渲染可点击的"查看目标/笔记/设备"链接。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
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
        title="第 {round_number} 轮已开始，已派出 {runs_created} 条笔记",
        body="笔记正在排队发布，预计 {eta_min} 分钟内完成。等发布后我会继续采集数据。",
    ),
    "goal.round.monitored": _NotifyMeta(
        severity="info",
        title="第 {round_number} 轮数据已回收",
        body="本轮 {notes_count} 条笔记的浏览、点赞等数据已保存，正在生成复盘。",
    ),
    "goal.round.decided": _NotifyMeta(
        severity="info",
        title="第 {round_number} 轮复盘完成",
        body="系统正在判断是继续下一轮还是收工，无需手动操作。",
    ),
    "goal.round.decided.continue": _NotifyMeta(
        severity="info",
        title="第 {round_number} 轮复盘：继续下一轮",
        body="表现还没达标，预计 {eta_min} 分钟后开始第 {next_round} 轮。",
    ),
    "goal.round.decided.done": _NotifyMeta(
        severity="success",
        title="目标已完成：{reason}",
        body="共跑了 {total_rounds} 轮，可点击下方「查看目标」看完整总结。",
    ),
    # publish_node 通知（Step 6）
    "note.published": _NotifyMeta(
        severity="success",
        title="笔记已发布：{title}",
        body="24 小时后会自动采集表现数据，可点击「查看笔记」跟踪效果。",
    ),
    # _do_collect 通知（Step 7）
    "note.collected": _NotifyMeta(
        severity="success",
        title="数据回收完成",
        body=(
            "浏览 {views} · 点赞 {likes} · 收藏 {collects} · "
            "评论 {comments} · 涨粉 {follows_gained}"
        ),
    ),
    "note.collect.failed": _NotifyMeta(
        severity="warning",
        title="数据回收失败",
        body="{reason_display}。调度器会自动重试，若多次失败请检查设备是否在线。",
    ),
    # 运行监控
    "agent_run_stuck_timeout": _NotifyMeta(
        severity="warning",
        title="任务执行超时",
        body="一条任务超过 {timeout_sec} 秒还没完成，已自动标记为失败。可查看对应 run 详情了解原因。",
    ),
    "goal_stuck_watchdog_rescued": _NotifyMeta(
        severity="success",
        title="卡住的目标已恢复",
        body="目标之前卡住超过 {threshold_sec} 秒，系统已自动把它推回正轨。",
    ),
    # 告警反饋（alert_scanner 產生）
    "DEVICE_OFFLINE": _NotifyMeta(
        severity="error",
        title="设备离线",
        body="设备已经超过 {threshold_min} 分钟没上报心跳。请检查手机网络、电量和 Matrix 应用是否在后台运行。",
    ),
    "RISK_BLOCKED": _NotifyMeta(
        severity="error",
        title="账号被平台风控",
        body="账号风险评分 {risk_score} 超过阈值，系统已自动暂停该账号，请人工确认后再恢复。",
    ),
    "SELECTOR_NOT_FOUND": _NotifyMeta(
        severity="warning",
        title="手机界面识别失败",
        body="{tool} 在小红书页面上找不到预期按钮，已连续失败 {fail_count} 次。系统会尝试用视觉模型兜底。",
    ),
    "TAILSCALE_DERP_LOST": _NotifyMeta(
        severity="error",
        title="组网中继异常",
        body="DERP 区域 {region} 不可达，服务器可能连不上手机。请检查 Headscale / DERP 容器状态。",
    ),
    "POSTGRES_DISK_FULL": _NotifyMeta(
        severity="warning",
        title="数据库磁盘空间不足",
        body="磁盘使用率 {disk_usage}% 已超过 {threshold}%，请清理旧的 checkpoint 或 heartbeat 记录。",
    ),
    # agent 内部 alert 节点通用 code（各具体错误通过 payload.code 区分）
    "agent.alert": _NotifyMeta(
        severity="warning",
        title="任务需要人工关注：{alert_title}",
        body="{alert_body}",
    ),
}


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
        meta = _NOTIFY_META.get(code)
        if meta is None:
            # 未注册 code 直接拒绝：宁可不发，也不给用户看 {code}/{payload}
            logger.error(
                "notifier.unregistered_code",
                code=code,
                payload=payload,
                hint="请在 _notifier_webhook._NOTIFY_META 注册人话文案",
            )
            return

        title, body = _render(meta, code, payload)

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
    if not payload:
        return {}
    return {
        k: (v if v is not None else "") if isinstance(v, (str, int, float)) else str(v)
        for k, v in payload.items()
    }


def _render(meta: _NotifyMeta, code: str, payload: dict[str, Any]) -> tuple[str, str]:
    """按 meta 模板渲染 title/body。

    对部分 code 做特殊转换（如错误码转人话、时间单位换算），
 保持模板本身只关心文案结构。
    """
    ctx = dict(_safe_format_map(payload))

    # agent.alert 节点：payload.code 是真实告警类型
    if code == "agent.alert":
        alert_code = str(ctx.get("code", ""))
        ctx["alert_title"] = _alert_title(alert_code, ctx)
        ctx["alert_body"] = _alert_body(alert_code, ctx)

    # 设备离线：把秒换算成分钟显示
    if code == "DEVICE_OFFLINE":
        try:
            sec = float(ctx.get("last_heartbeat_age_sec", 0))
        except (ValueError, TypeError):
            sec = 0
        ctx["threshold_min"] = max(1, round(sec / 60))

    # 采集失败 reason 转人话
    if code == "note.collect.failed":
        ctx["reason_display"] = _collect_fail_reason(str(ctx.get("reason", "")))

    # 任务超时
    if code == "agent_run_stuck_timeout":
        try:
            ctx["timeout_sec"] = int(float(ctx.get("timeout_sec", 120)))
        except (ValueError, TypeError):
            ctx["timeout_sec"] = 120

    # 目标恢复
    if code == "goal_stuck_watchdog_rescued":
        try:
            ctx["threshold_sec"] = int(float(ctx.get("threshold_sec", 300)))
        except (ValueError, TypeError):
            ctx["threshold_sec"] = 300

    # 目标结束原因（_should_continue 返回的是英文内部 reason）
    if code == "goal.round.decided.done":
        ctx["reason"] = _humanize_done_reason(str(ctx.get("reason", "")))

    try:
        title = meta.title.format(**ctx)
        body = meta.body.format(**ctx)
    except (KeyError, IndexError):
        # 理论上不会到这一步（模板只使用自己声明的占位），
        # 但为了防止缺字段导致整段文案消失，退到未渲染模板。
        logger.warning("notifier.render_failed", code=code, payload=payload)
        title = meta.title
        body = meta.body

    return title, body


def _humanize_done_reason(reason: str) -> str:
    """把 _should_continue 返回的英文 reason 翻译成人话。"""
    if reason.startswith("max_rounds reached:"):
        try:
            n = int(reason.split(":", 1)[1].strip())
        except (ValueError, IndexError):
            n = None
        return f"已跑满 {n} 轮" if n else "已跑满设定轮数"
    if reason.startswith("deadline reached:"):
        ts = reason.split(":", 1)[1].strip() if ":" in reason else ""
        return f"截止时间已到（{ts[:19]}）" if ts else "截止时间已到"
    if reason.startswith("KPI achieved:"):
        kpi = reason.split(":", 1)[1].strip() if ":" in reason else ""
        return f"KPI 已达标（{kpi}）" if kpi else "KPI 已达标"
    return reason


def _alert_title(alert_code: str, ctx: dict[str, Any]) -> str:
    """把 agent.alert payload 里的错误码转成人话标题。"""
    titles = {
        "KB_RETRIEVE_FAILED": "知识库读不到",
        "LLM_FAILED": "AI 服务异常",
        "DRAFT_LLM_FAILED": "写稿失败",
        "REVISE_LLM_FAILED": "改稿失败",
        "PUBLISH_FAILED": "发布失败",
        "RISK_BLOCKED": "账号被风控",
        "DEVICE_OFFLINE": "设备离线",
        "OUT_OF_ACTIVE_WINDOW": "不在可发布时间窗口",
        "CIRCUIT_OPEN": "设备任务熔断中",
        "EXECUTOR_EXCEPTION": "设备任务执行异常",
        "UPLOAD_FAILED": "笔记上传失败",
        "TASK_TIMEOUT": "任务执行超时",
    }
    return titles.get(alert_code, alert_code.replace("_", " "))


def _alert_body(alert_code: str, ctx: dict[str, Any]) -> str:
    """把 agent.alert payload 里的错误码转成人话正文。"""
    message = str(ctx.get("message", ""))
    run_id = str(ctx.get("run_id", ""))[:8]
    default = f"{message}（run {run_id}）" if message else f"运行出错，需要查看 run {run_id}。"

    bodies = {
        "PUBLISH_FAILED": "笔记没发出去，请检查手机是否在小红书页面卡住了。",
        "UPLOAD_FAILED": "图片或文字上传失败，可能是网络问题，系统会按策略重试。",
        "TASK_TIMEOUT": "任务执行太久没完成，请检查手机运行是否流畅。",
        "DEVICE_OFFLINE": "设备离线，任务无法继续。请先让设备重新上线。",
        "RISK_BLOCKED": "账号被平台风控，系统已暂停后续操作，请人工确认。",
        "CIRCUIT_OPEN": "这台设备最近失败太多，调度器先让它冷静一下，稍后再恢复。",
        "EXECUTOR_EXCEPTION": f"执行器报错了：{message[:120]}。",
        "OUT_OF_ACTIVE_WINDOW": "当前不在允许发布的时间段内，会等到下个窗口再试。",
        "LLM_FAILED": "AI 服务暂时不可用，系统会稍后重试。",
        "DRAFT_LLM_FAILED": "AI 写稿失败，会重新生成。",
        "REVISE_LLM_FAILED": "AI 改稿失败，审稿结果可能无法应用。",
        "KB_RETRIEVE_FAILED": "知识库暂时读不到，这次可能不会参考历史经验。",
    }
    return bodies.get(alert_code, default)


def _collect_fail_reason(reason: str) -> str:
    """把采集失败的内部 reason 转成人话。"""
    mapping = {
        "device_collector_exception": "设备在采集数据时抛异常",
        "collector_returned_non_dict": "设备返回的数据格式不对",
        "persist_exception": "服务端保存数据失败",
        "metrics_missing": "返回的数据里没有浏览/点赞等字段",
        "device_offline": "设备离线",
        "platform_rejected": "小红书拒绝了请求",
    }
    return mapping.get(reason, f"采集失败（{reason}）")


__all__ = ["WebhookNotifier", "DEFAULT_RECIPIENT", "ConfigReaderFn"]
