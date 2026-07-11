"""Agent run worker。

问题：路由只插 ``agent_runs`` 行（status=running），没人去调起 LangGraph 状态机。
本模块提供一个后台 asyncio 协程，定时扫 ``agent_runs`` 表 status=running 的行，
对每条新行调 ``RunManager.start_run``，并跟踪 in-flight run 防止重复启动。

启动方式：在 FastAPI lifespan 里 ``asyncio.create_task(run_worker())``；
关闭时 ``stop_event.set()`` 优雅停。
"""
from __future__ import annotations

import asyncio
from matrix.monitoring.logging import get_logger
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.agent.run_manager import get_manager
from matrix.db.models import AgentRun

logger = get_logger(__name__)


class AgentRunWorker:
    """Agent run 启动 worker（单例）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval: float = 1.0,
        max_retries: int = 5,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError(f"poll_interval must be > 0, got {poll_interval}")
        if max_retries < 1:
            raise ValueError(f"max_retries must be >= 1, got {max_retries}")
        self._session_factory = session_factory
        self._poll_interval = poll_interval
        self._max_retries = max_retries
        self._stop_event: asyncio.Event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        # 防止重复启动同一 run（同一 run_id 只起一次协程）
        self._in_flight: set = set()
        # 失败重试计数：run_id -> 失败次数；in-memory，重启清零
        self._retry_count: dict = {}

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _scan_once(self) -> None:
        """扫一次 DB，挑出新 running run（in_flight 集合中不存在的）拉起。"""
        async with self._session_factory() as session:
            stmt = (
                select(AgentRun)
                .where(AgentRun.status == "running")
                .order_by(AgentRun.started_at.asc())
                .limit(50)
            )
            rows = (await session.execute(stmt)).scalars().all()

        for r in rows:
            if r.id in self._in_flight:
                continue
            self._in_flight.add(r.id)
            asyncio.create_task(self._run_one(r.id))

    async def _run_one(self, run_id) -> None:
        """拉起一条 run 的状态机。失败也不阻塞 worker 循环。

        失败重试计数 in-memory，避免对同一条坏 run 每秒重试；
        达到 ``max_retries`` 后让 watchdog（默认 10 分钟阈值）标 timeout。
        """
        try:
            manager = get_manager()
            await manager.start_run(run_id)
            # 成功：清掉失败计数
            self._retry_count.pop(run_id, None)
            logger.info("agent_run_worker.completed", run_id=run_id)
        except Exception:
            self._retry_count[run_id] = self._retry_count.get(run_id, 0) + 1
            logger.exception(
                "agent_run_worker.failed",
                run_id=run_id,
                retries=self._retry_count[run_id],
                max_retries=self._max_retries,
            )
            if self._retry_count[run_id] >= self._max_retries:
                # 本地重试耗尽；不再积极重试，让 watchdog 兜底标 timeout
                logger.warning(
                    "agent_run_worker.retries_exhausted",
                    run_id=run_id,
                    retries=self._retry_count[run_id],
                )
        finally:
            self._in_flight.discard(run_id)

    async def loop(self) -> None:
        """worker 主循环。"""
        logger.info(
            "agent_run_worker.started", poll_interval=self._poll_interval
        )
        while not self._stop_event.is_set():
            try:
                await self._scan_once()
            except Exception:
                logger.exception("agent_run_worker.scan_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                pass
        logger.info("agent_run_worker.stopped")

    def start(self) -> None:
        """在后台起 worker 协程（fire-and-forget）。"""
        if self.is_running:
            logger.warning("agent_run_worker already running")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self.loop(), name="agent-run-worker")

    async def stop(self) -> None:
        """停 worker 并等待其结束。"""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_worker: AgentRunWorker | None = None


def set_worker(worker: AgentRunWorker) -> None:
    global _worker
    _worker = worker


def get_worker() -> AgentRunWorker:
    if _worker is None:
        raise RuntimeError("AgentRunWorker not initialized")
    return _worker


__all__ = ["AgentRunWorker", "set_worker", "get_worker"]
