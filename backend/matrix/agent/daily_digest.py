"""AI 每日日报：把过去 24 小时通知汇总成一段人话总结。

设计要点：
- 复用默认 LLM client（MATRIX_LLM_PROVIDER / MATRIX_LLM_MODEL）。
- 按业务维度生成日报：每个有消息的业务各一条，避免多业务混成一团。
- 只在有通知的日子生成；没有通知的业务跳过。
- 写入 notifications 表（code=daily.digest），前端置顶显示。
- 失败只记日志，不抛异常，不挡主流程。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.db.models import Business, Notification
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class DailyDigestConfig:
    """日报运行时参数。"""

    def __init__(
        self,
        *,
        poll_interval_sec: float = 60.0,
        hour: int = 9,
        minute: int = 0,
        max_notifications: int = 200,
        max_tokens: int = 600,
        model: str | None = None,
    ) -> None:
        self.poll_interval_sec = poll_interval_sec
        self.hour = hour
        self.minute = minute
        self.max_notifications = max_notifications
        self.max_tokens = max_tokens
        self.model = model


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """你是一位自媒体运营助手，正在给老板写每日运营日报。
要求：
1. 用轻松、专业、像人话的中文写，禁止出现代码、内部字段名（如 run_id、note_metrics、max_rounds 等）。
2. 先给一句总体概况：昨天/今天共发布了多少条笔记、整体数据如何、有没有异常。
3. 再列出「需要关注」的事项（失败、超时、设备离线、风控等），没有就写"暂无异常"。
4. 每个关注点要说明：发生了什么、对老板意味着什么、建议做什么。
5. 总字数控制在 150-250 字。
"""


def _format_notifications(items: list[Notification]) -> str:
    lines: list[str] = []
    for n in items:
        ts = n.created_at.strftime("%m-%d %H:%M") if n.created_at else "?"
        lines.append(f"[{ts}] {n.severity} | {n.title}\n{n.body}")
    return "\n---\n".join(lines)


def _build_prompt(business_name: str | None, items: list[Notification]) -> str:
    header = f"业务：{business_name or '默认业务'}\n"
    return header + "过去 24 小时运营事件如下：\n\n" + _format_notifications(items)


# ---------------------------------------------------------------------------
# 生成 + 写入
# ---------------------------------------------------------------------------


class DailyDigestGenerator:
    """日报生成器：查数据 → 调 LLM → 写 notifications。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm_client: Any,
        config: DailyDigestConfig | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._llm_client = llm_client
        self._config = config or DailyDigestConfig()

    async def run_once(self, now: datetime | None = None) -> int:
        """立即跑一轮日报生成，返回成功生成的条数。"""
        now = now or datetime.now(UTC)
        since = now - timedelta(hours=24)
        created = 0

        async with self._session_factory() as session:
            business_rows = await self._list_businesses(session)
            for business_id, business_name in business_rows:
                items = await self._load_notifications(
                    session, since=since, business_id=business_id
                )
                if not items:
                    continue
                try:
                    await self._generate_and_write(
                        session,
                        business_id=business_id,
                        business_name=business_name,
                        items=items,
                        now=now,
                    )
                    created += 1
                except Exception:
                    logger.exception(
                        "daily_digest.generate_failed",
                        business_id=business_id,
                    )
            await session.commit()

        return created

    async def _list_businesses(
        self, session: AsyncSession
    ) -> list[tuple[str | None, str | None]]:
        """返回 [(business_id, business_name), ...]，第一项是 None 表示无业务归属。"""
        stmt = select(Business.id, Business.name).where(
            Business.status == "active"
        )
        rows = (await session.execute(stmt)).all()
        result: list[tuple[str | None, str | None]] = [(None, None)]
        result.extend((str(r[0]), r[1]) for r in rows)
        return result

    async def _load_notifications(
        self,
        session: AsyncSession,
        *,
        since: datetime,
        business_id: str | None,
    ) -> list[Notification]:
        """加载某业务过去 24 小时的通知，排除日报自身（避免自我循环）。"""
        stmt = (
            select(Notification)
            .where(
                Notification.created_at >= since,
                Notification.code != "daily.digest",
            )
            .order_by(Notification.created_at.desc())
            .limit(self._config.max_notifications)
        )
        # notifications 表目前没有 business_id 列；用 payload 里的 business_id 过滤
        rows = (await session.execute(stmt)).scalars().all()
        filtered: list[Notification] = []
        for r in rows:
            payload_bid = (r.payload or {}).get("business_id")
            row_bid = str(payload_bid) if payload_bid else None
            if row_bid == business_id:
                filtered.append(r)
        return filtered

    async def _generate_and_write(
        self,
        session: AsyncSession,
        *,
        business_id: str | None,
        business_name: str | None,
        items: list[Notification],
        now: datetime,
    ) -> None:
        prompt = _build_prompt(business_name, items)
        result = await self._llm_client.complete(
            prompt,
            model=self._config.model or _default_model(),
            system=_SYSTEM_PROMPT,
            max_tokens=self._config.max_tokens,
            temperature=0.6,
            call_type="daily_digest",
        )
        text = result.text.strip()
        # 取第一行做标题，剩余做正文
        lines = text.splitlines()
        title = lines[0][:128] if lines else "每日运营日报"
        body = "\n".join(lines[1:]).strip() or text

        payload: dict[str, Any] = {
            "generated_at": now.isoformat(),
            "source_count": len(items),
        }
        if business_id:
            payload["business_id"] = business_id

        notification = Notification(
            recipient="operator",
            code="daily.digest",
            severity="info",
            title=title,
            body=body,
            payload=payload,
        )
        session.add(notification)
        logger.info(
            "daily_digest.wrote",
            business_id=business_id,
            title=title,
            tokens=result.completion_tokens,
        )


def _default_model() -> str:
    """不指定 model 时，走一个便宜且足够用的默认模型。"""
    from matrix.llm.router import get_default_client

    # get_default_client 只返回 client，不返回 model；这里用环境常见默认
    import os

    return os.environ.get("MATRIX_LLM_MODEL") or "qwen-plus"


# ---------------------------------------------------------------------------
# Worker：每日定时跑
# ---------------------------------------------------------------------------


class DailyDigestWorker:
    """后台 worker：每天固定时间触发日报生成。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm_client: Any,
        config: DailyDigestConfig | None = None,
    ) -> None:
        self._generator = DailyDigestGenerator(
            session_factory=session_factory,
            llm_client=llm_client,
            config=config,
        )
        self._config = config or DailyDigestConfig()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> asyncio.Task[None]:
        if self._task is not None and not self._task.done():
            return self._task
        self._stop_event.clear()
        self._task = asyncio.create_task(self._loop(), name="daily-digest")
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now(UTC)
            next_run = self._next_run(now)
            wait_sec = (next_run - now).total_seconds()
            logger.info(
                "daily_digest.next_run",
                next_run=next_run.isoformat(),
                wait_sec=wait_sec,
            )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=wait_sec)
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                return
            try:
                await self._generator.run_once()
            except Exception:
                logger.exception("daily_digest.tick_failed")

    def _next_run(self, now: datetime) -> datetime:
        """计算下一个触发时间（跨天处理）。"""
        nxt = now.replace(hour=self._config.hour, minute=self._config.minute, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        return nxt


__all__ = [
    "DailyDigestConfig",
    "DailyDigestGenerator",
    "DailyDigestWorker",
]
