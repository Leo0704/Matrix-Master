"""Goal orchestrator worker：每秒扫 phase≠DONE 的 goal，调 advance_goal 推进。

仿 AgentRunWorker 模式（runner.py）。
"""

from __future__ import annotations

import asyncio
import uuid
from matrix.monitoring.logging import get_logger
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.agent.orchestrator import advance_goal
from matrix.db.models import Goal
from matrix.db.session import get_session

logger = get_logger(__name__)


class GoalOrchestratorWorker:
    """Goal-level orchestrator 推进 worker（单例）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval: float = 5.0,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError(f"poll_interval must be > 0, got {poll_interval}")
        self._session_factory = session_factory
        self._poll_interval = poll_interval
        self._stop_event: asyncio.Event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        # 防止并发推进同一 goal
        self._in_flight: set[uuid.UUID] = set()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _scan_once(self) -> None:
        async with self._session_factory() as session:
            stmt = (
                select(Goal)
                .where(
                    Goal.deleted_at.is_(None),
                    Goal.status == "active",
                    Goal.phase != "DONE",
                )
                .order_by(Goal.created_at.asc())
                .limit(20)
            )
            goals = (await session.execute(stmt)).scalars().all()

        for g in goals:
            if g.id in self._in_flight:
                continue
            self._in_flight.add(g.id)
            asyncio.create_task(self._advance_one(g.id))

    async def _advance_one(self, goal_id: uuid.UUID) -> None:
        try:
            # 用 get_session() 上下文管理器：干净退出自动 commit，异常回滚。
            # 避免 advance_goal 内部某个分支忘记 commit 导致 phase 永远卡住。
            async with get_session() as session:
                g = await session.get(Goal, goal_id)
                if g is None or g.deleted_at is not None or g.phase == "DONE":
                    return
                result = await advance_goal(session, g)
                if result is not None and result.phase_before != result.phase_after:
                    logger.info(
                        "orchestrator.advanced",
                        goal_id=str(goal_id),
                        before=result.phase_before,
                        after=result.phase_after,
                        round=result.round_number,
                        action=result.action,
                    )
        except Exception:
            logger.exception("orchestrator.advance_failed", goal_id=str(goal_id))
        finally:
            self._in_flight.discard(goal_id)

    async def loop(self) -> None:
        logger.info("goal_orchestrator.started", poll_interval=self._poll_interval)
        while not self._stop_event.is_set():
            try:
                await self._scan_once()
            except Exception:
                logger.exception("goal_orchestrator.scan_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                pass
        logger.info("goal_orchestrator.stopped")

    def start(self) -> None:
        if self.is_running:
            logger.warning("goal_orchestrator already running")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self.loop(), name="goal-orchestrator")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
            self._task = None


# 全局单例
_worker: GoalOrchestratorWorker | None = None


def set_orchestrator_worker(worker: GoalOrchestratorWorker) -> None:
    global _worker
    _worker = worker


def get_orchestrator_worker() -> GoalOrchestratorWorker:
    if _worker is None:
        raise RuntimeError("GoalOrchestratorWorker not initialized")
    return _worker


__all__ = ["GoalOrchestratorWorker", "set_orchestrator_worker", "get_orchestrator_worker"]
