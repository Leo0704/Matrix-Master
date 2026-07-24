"""任务调度器（按 SDD §3.4.2）。

每 1s 拉一次 pending task，标记 running 后委派给限速器执行。
DB 集成由 ``TaskLoader`` / ``TaskExecutor`` 抽象，调度器本身不连 DB。
"""
from __future__ import annotations

import asyncio
from matrix.monitoring.logging import get_logger
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

from .circuit_breaker import CircuitOpen
from .rate_limiter import RateLimiter

logger = get_logger(__name__)


class TaskLike(Protocol):
    id: object
    plan_id: object
    device_id: object
    account_id: object
    action: str
    payload: dict
    request_id: str
    status: str
    attempts: int
    last_error: dict | None
    scheduled_at: datetime
    executed_at: datetime | None


@dataclass
class TaskResult:
    ok: bool
    error: dict | None = None


class TaskLoader(Protocol):
    """集成层实现：从 DB 拉取到期的 pending tasks。"""

    async def load_pending(self, now: datetime, limit: int) -> list[TaskLike]: ...


class TaskStatusWriter(Protocol):
    """集成层实现：把 status / executed_at / last_error 写回 DB。"""

    async def mark_running(self, task: TaskLike) -> None: ...
    async def mark_success(self, task: TaskLike, executed_at: datetime) -> None: ...
    async def mark_failed(self, task: TaskLike, error: dict, executed_at: datetime) -> None: ...
    async def mark_pending(self, task: TaskLike, scheduled_at: datetime) -> None: ...


class TaskExecutor(Protocol):
    """集成层实现：实际调用 APK。返回 ok=True/False。"""

    async def execute(self, task: TaskLike) -> bool: ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Scheduler:
    loader: TaskLoader
    writer: TaskStatusWriter
    executor: TaskExecutor
    rate_limiter: RateLimiter
    poll_interval: float = 1.0
    batch_size: int = 100
    clock: Callable[[], datetime] = _utcnow
    # W3：卡死 running 任务回收。sweep_interval 秒扫一次；running 超过
    # stale_running_seconds 的任务退回 pending（attempts 已自增），
    # attempts 达到 max_attempts 才标 failed。
    sweep_interval: float = 60.0
    stale_running_seconds: float = 1800.0
    max_attempts: int = 5
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def run(self) -> None:
        """主循环。每 poll_interval 秒拉一批任务并发 dispatch。"""
        logger.info("scheduler starting")
        last_sweep = 0.0
        while not self._stop_event.is_set():
            loop_start = asyncio.get_running_loop().time()
            if loop_start - last_sweep >= self.sweep_interval:
                last_sweep = loop_start
                await self._sweep_stale_running()

            try:
                now = self.clock()
                ready = await self.loader.load_pending(now, self.batch_size)
            except Exception:
                logger.exception("loader.load_pending failed")
                ready = []

            if ready:
                await asyncio.gather(
                    *(self._dispatch(t) for t in ready),
                    return_exceptions=True,
                )

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
        logger.info("scheduler stopped")

    def stop(self) -> None:
        self._stop_event.set()

    async def _sweep_stale_running(self) -> None:
        """定期回收卡死 running 任务（loader 实现了 reclaim 才执行）。"""
        reclaim = getattr(self.loader, "reclaim_stale_running", None)
        if reclaim is None:
            return
        try:
            reclaimed = await reclaim(
                self.clock(),
                stale_after_seconds=self.stale_running_seconds,
                max_attempts=self.max_attempts,
            )
        except Exception:
            logger.exception("loader.reclaim_stale_running failed")
            return
        if reclaimed:
            logger.warning("scheduler.reclaimed_stale_running", count=reclaimed)

    async def _dispatch(self, task: TaskLike) -> None:
        # v0.7 Phase 6：device_publish 由手机主动拉取，调度器不碰。
        if task.action == "device_publish":
            logger.debug("scheduler.skip_pull_action", task_id=task.id, action=task.action)
            return

        await self.writer.mark_running(task)
        try:
            ok = await self.executor.execute(task)
        except CircuitOpen as e:
            # W3：设备熔断中不算任务失败——退回 pending，scheduled_at 推迟到
            # 冷却结束后，等熔断恢复再执行。
            retry_at = self.clock() + timedelta(seconds=e.retry_after)
            logger.info(
                "scheduler.circuit_open_reschedule",
                task_id=task.id,
                retry_after=round(e.retry_after, 1),
            )
            await self.writer.mark_pending(task, retry_at)
            return
        except Exception as e:
            logger.exception("executor raised", task_id=task.id)
            await self.writer.mark_failed(
                task,
                {"code": "EXECUTOR_RAISED", "message": str(e)},
                self.clock(),
            )
            return

        if ok:
            await self.writer.mark_success(task, self.clock())
        else:
            await self.writer.mark_failed(
                task,
                {"code": "EXECUTOR_FALSE", "message": "executor returned false"},
                self.clock(),
            )
