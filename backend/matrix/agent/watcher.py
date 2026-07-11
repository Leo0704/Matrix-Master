"""v0.7 Phase 4：AgentRunWatchdog。

问题：当前只有 ``AgentRunWorker`` 拉起协程跑 run；如果协程卡死或 worker
进程崩溃，run 一直停在 status='running'，没有兜底。

兜底机制：本模块实现一个 asyncio watchdog，每 30s 扫一次 DB：

  SELECT id FROM agent_runs
   WHERE status='running'
     AND ended_at IS NULL
     AND updated_at < NOW() - :threshold_sec
  → 把 status 改为 'timeout'
  → 写 alert_logs（通过 ``services.heartbeat_writer`` 之外，独立路径）

设计参考：``AgentRunWorker``（runner.py）和 ``Scheduler.run`` 都是
asyncio loop + ``asyncio.wait_for(stop_event, timeout=poll_interval)``
模式，本模块沿用之。

注意：依赖 ``agent_runs.updated_at`` 触发器（migration 001 里建），
或依赖 ``AgentServices.heartbeat_writer`` 在节点切换时被调用。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class WatchdogConfig:
    """watchdog 阈值集合（产线可在 app_config 调）。"""

    poll_interval_sec: float = 30.0
    stuck_threshold_sec: int = 600  # 10 min
    dry_run: bool = False  # 默认开启真实标 timeout，作为 worker 失败重试耗尽后的兜底


class _ScannerProtocol:
    """扫描器协议；DB 默认实现见 ``AgentRunScanner``（集成层）。

    Protocol 用 duck typing，避免循环 import。
    """

    async def find_stuck_runs(
        self, now: datetime, threshold_sec: int
    ) -> list[Any]: ...

    async def mark_timeout(
        self, run_id: Any, now: datetime, reason: str
    ) -> None: ...


class AgentRunScanner:
    """DB 默认扫描器（基于 SQLAlchemy AsyncSession）。

    判定逻辑：``status='running' AND ended_at IS NULL AND
    started_at < now - threshold_sec``

    用 ``started_at`` 而非 ``updated_at`` 是因为中间状态切换不写
    ``updated_at``（避免给所有 node 加 IO），started_at 是
    "从这一刻起就没结论" 的可靠下界。
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def find_stuck_runs(
        self, now: datetime, threshold_sec: int
    ) -> list[Any]:
        from sqlalchemy import select

        from matrix.db.models import AgentRun

        cutoff = now.timestamp() - threshold_sec
        cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
        try:
            async with self._session_factory() as session:
                stmt = (
                    select(AgentRun.id)
                    .where(AgentRun.status == "running")
                    .where(AgentRun.ended_at.is_(None))
                    .where(AgentRun.started_at < cutoff_dt)
                )
                result = await session.execute(stmt)
                return [row[0] for row in result.all()]
        except Exception:
            logger.exception("watchdog.find_stuck_runs failed")
            return []

    async def mark_timeout(
        self, run_id: Any, now: datetime, reason: str
    ) -> None:
        from sqlalchemy import update

        from matrix.db.models import AgentRun

        try:
            async with self._session_factory() as session:
                stmt = (
                    update(AgentRun)
                    .where(AgentRun.id == run_id)
                    .values(
                        status="timeout",
                        ended_at=now,
                    )
                )
                await session.execute(stmt)
                await session.commit()
        except Exception:
            logger.exception("watchdog.mark_timeout failed for %s", run_id)


class AgentRunWatchdog:
    """asyncio 周期扫描 + 标 timeout + 写 alert。"""

    def __init__(
        self,
        scanner: Any,
        *,
        config: WatchdogConfig | None = None,
        notifier: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        # scanner 兼容 _ScannerProtocol duck type（DB 集成层用 AgentRunScanner）
        self._scanner = scanner
        self.config = config or WatchdogConfig()
        self._notifier = notifier
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def configure_from_config_reader(
        self,
        config_reader: Callable[[str, Any], Any] | None,
    ) -> None:
        """从 ``app_config`` 表读 watchdog 配置覆盖默认值。

        P2-1：让运维能在不重启的情况下通过 PATCH /settings 调阈值。
        不传 config_reader 时保留硬编码默认（生产老路径兼容）。
        """
        if config_reader is None:
            return
        try:
            self.config.dry_run = await config_reader(
                "agent_watchdog.dry_run", self.config.dry_run
            )
            self.config.stuck_threshold_sec = int(
                await config_reader(
                    "agent_watchdog.stuck_threshold_sec", self.config.stuck_threshold_sec
                )
            )
            self.config.poll_interval_sec = float(
                await config_reader(
                    "agent_watchdog.poll_interval_sec", self.config.poll_interval_sec
                )
            )
        except Exception:
            logger.exception("watchdog.configure_from_config_reader failed; keep defaults")

    async def _scan_once(self) -> int:
        """一次扫描，返回标 timeout 的 run 数。"""
        now = _utcnow()
        try:
            stuck = await self._scanner.find_stuck_runs(
                now, self.config.stuck_threshold_sec
            )
        except Exception:
            logger.exception("watchdog.scan_failed")
            return 0
        if not stuck:
            return 0

        count = 0
        for run_id in stuck:
            reason = f"watchdog_timeout (>{self.config.stuck_threshold_sec}s)"
            if self.config.dry_run:
                logger.warning(
                    "watchdog.dry_run would_mark_timeout run_id=%s reason=%s",
                    run_id,
                    reason,
                )
                continue
            try:
                await self._scanner.mark_timeout(run_id, now, reason)
            except Exception:
                logger.exception("watchdog.mark_timeout_failed run_id=%s", run_id)
                continue
            if self._notifier is not None:
                try:
                    maybe_coro = self._notifier(
                        "agent_run_stuck_timeout",
                        {"run_id": str(run_id), "reason": reason},
                    )
                    if hasattr(maybe_coro, "__await__"):
                        await maybe_coro
                except Exception:
                    logger.exception("watchdog.notifier_failed")
            count += 1
            logger.warning("watchdog.marked_timeout run_id=%s", run_id)
        return count

    async def loop(self) -> None:
        """主循环（asyncio.wait_for + 取消支持）。"""
        logger.info(
            "watchdog.starting interval=%ss threshold=%ss dry_run=%s",
            self.config.poll_interval_sec,
            self.config.stuck_threshold_sec,
            self.config.dry_run,
        )
        while not self._stop_event.is_set():
            try:
                await self._scan_once()
            except Exception:
                logger.exception("watchdog.scan_iteration_crashed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.poll_interval_sec,
                )
            except asyncio.TimeoutError:
                pass
        logger.info("watchdog.stopped")

    def start(self) -> asyncio.Task:
        """启动后台 task。"""
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self.loop(), name="agent-run-watchdog"
            )
        return self._task

    async def stop(self) -> None:
        """优雅停。"""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()


__all__ = [
    "AgentRunWatchdog",
    "AgentRunScanner",
    "WatchdogConfig",
]
