"""GoalStuckWatchdog：phase='PENDING' 卡死的目标救援路径（P2-2）。

问题：上一轮 P0-1 把 create_goal 里的"启动种子 AgentRun"删干净之后，
GoalOrchestratorWorker 是唯一能把新 goal 从 PENDING 推到 PREPARING 的发动机。
它一旦静默死掉，新 goal 会永久卡死。

兜底：每 60s（比协调员主循环 5s 慢一个数量级）扫一次 DB，挑出
``status='active' AND phase='PENDING' AND deleted_at IS NULL
  AND created_at < now - 120s AND phase_updated_at IS NULL``
的目标（"协调员从来没碰过我"的干净信号），逐个调 ``advance_goal`` 推进。
复用主流程一致的事务 / commit 语义，不另写 UPDATE（避免 schema drift）。

镜像 ``matrix.agent.watcher.AgentRunWatchdog`` 的 3 件套结构（Config + Scanner + Watchdog），
方便后续把它当模板改造成别的兜底。

参考设计动机：本模块配套 GoalOrchestratorWorker 自愈（loop 外层 session + start() respawn）
一起构成两道防线：
  - 协调员自己抛过 BaseException 退避重试（瞬时错误）
  - 协调员整个进程级崩/没启动（这个 watchdog 救）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class GoalStuckWatchdogConfig:
    """看门狗阈值集合（产线可在 app_config 调）。"""

    poll_interval_sec: float = 60.0
    # 120s = 24 个协调员 tick 没动过：短的会和主路径竞争，长的救不到近实时孤儿
    stuck_threshold_sec: int = 120
    # 默认不 dry_run：orchestrator 死了就该被救上来
    dry_run: bool = False
    # 单 tick 推进上限：与 orchestrator._scan_once() 一致防 spike
    max_per_tick: int = 20


# ---------------------------------------------------------------------------
# Scanner protocol + DB 默认实现
# ---------------------------------------------------------------------------


class _ScannerProtocol:
    """扫描器协议；DB 默认实现见 :class:`GoalStuckScanner`（集成层）。"""

    async def find_stuck_pending(
        self, now: datetime, threshold_sec: int
    ) -> list[Any]: ...

    async def advance_one(self, goal_id: Any) -> bool: ...


class GoalStuckScanner:
    """DB 默认扫描器：扫孤儿 PENDING + 推进。

    ``find_stuck_pending`` 选 ``phase_updated_at IS NULL`` 而不是 ``<``：
    ``phase_updated_at`` 只在 ``_set_phase``（orchestrator._set_phase）写，
    create_goal 永远不写。所以 NULL = "协调员压根没碰过我" 的最准信号，
    不会被"早 transition 完又回到 PENDING"的合法目标误判。
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def find_stuck_pending(
        self, now: datetime, threshold_sec: int
    ) -> list[Any]:
        from sqlalchemy import text

        cutoff = now - timedelta(seconds=threshold_sec)
        try:
            async with self._session_factory() as session:
                stmt = text(
                    """
                    SELECT id FROM goals
                     WHERE status = 'active'
                       AND phase = 'PENDING'
                       AND deleted_at IS NULL
                       AND created_at < :cutoff
                       AND phase_updated_at IS NULL
                     ORDER BY created_at ASC
                     LIMIT 20
                    """
                )
                result = await session.execute(stmt, {"cutoff": cutoff})
                return [row[0] for row in result.all()]
        except Exception:
            logger.exception("goal_stuck_watchdog.find_stuck_pending failed")
            return []

    async def advance_one(self, goal_id: Any) -> bool:
        """复用主流程 advance_goal + get_session()，事务/提交/回滚全一致。"""
        from matrix.agent.orchestrator import advance_goal
        from matrix.db.models import Goal
        from matrix.db.session import get_session

        try:
            async with get_session() as session:
                g = await session.get(Goal, goal_id)
                if g is None or g.deleted_at is not None or g.phase == "DONE":
                    return False
                result = await advance_goal(session, g)
                if result is None:
                    return False
                changed = result.phase_before != result.phase_after
                if changed:
                    logger.info(
                        "goal_stuck_watchdog.rescued",
                        goal_id=str(goal_id),
                        before=result.phase_before,
                        after=result.phase_after,
                        round=result.round_number,
                    )
                return changed
        except Exception:
            logger.exception(
                "goal_stuck_watchdog.advance_one failed goal_id=%s", goal_id
            )
            return False


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


class GoalStuckWatchdog:
    """``AgentRunWatchdog`` 的 goal-级孪生兄弟（结构照抄）。"""

    def __init__(
        self,
        scanner: Any,
        *,
        config: GoalStuckWatchdogConfig | None = None,
        notifier: Callable[..., Any] | None = None,
    ) -> None:
        self._scanner = scanner
        self.config = config or GoalStuckWatchdogConfig()
        self._notifier = notifier
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def configure_from_config_reader(
        self,
        config_reader: Callable[[str, Any], Any] | None,
    ) -> None:
        """从 app_config 读覆盖默认值；失败保留默认（与 watcher 同模式）。"""
        if config_reader is None:
            return
        try:
            self.config.dry_run = await config_reader(
                "goal_stuck_watchdog.dry_run", self.config.dry_run
            )
            self.config.poll_interval_sec = float(
                await config_reader(
                    "goal_stuck_watchdog.poll_interval_sec",
                    self.config.poll_interval_sec,
                )
            )
            self.config.stuck_threshold_sec = int(
                await config_reader(
                    "goal_stuck_watchdog.stuck_threshold_sec",
                    self.config.stuck_threshold_sec,
                )
            )
        except Exception:
            logger.exception(
                "goal_stuck_watchdog.configure_from_config_reader failed; keep defaults"
            )

    async def _scan_once(self) -> int:
        """一次扫描，返回本 tick 推进的目标数。"""
        now = _utcnow()
        try:
            stuck = await self._scanner.find_stuck_pending(
                now, self.config.stuck_threshold_sec
            )
        except Exception:
            logger.exception("goal_stuck_watchdog.scan_failed")
            return 0
        if not stuck:
            return 0

        rescued = 0
        for gid in stuck[: self.config.max_per_tick]:
            if self.config.dry_run:
                logger.warning(
                    "goal_stuck_watchdog.dry_run would_advance",
                    goal_id=str(gid),
                    threshold=self.config.stuck_threshold_sec,
                )
                continue
            try:
                ok = await self._scanner.advance_one(gid)
            except Exception:
                # 单目标异常不阻断整轮：scanner 自己已经 try/except，
                # 但如果调用方塞了非 scanner 而是会抛的自定义对象，再兜一层
                logger.exception(
                    "goal_stuck_watchdog.advance_one.raised goal_id=%s", gid
                )
                continue
            if ok:
                rescued += 1
                if self._notifier is not None:
                    try:
                        maybe_coro = self._notifier(
                            "goal_stuck_watchdog_rescued",
                            {
                                "goal_id": str(gid),
                                "threshold_sec": self.config.stuck_threshold_sec,
                            },
                        )
                        if hasattr(maybe_coro, "__await__"):
                            await maybe_coro
                    except Exception:
                        logger.exception("goal_stuck_watchdog.notifier_failed")
        return rescued

    async def loop(self) -> None:
        """主循环：会话级 try/except + 内部 sleep_until_stop 模式。"""
        logger.info(
            "goal_stuck_watchdog.starting interval=%ss threshold=%ss dry_run=%s",
            self.config.poll_interval_sec,
            self.config.stuck_threshold_sec,
            self.config.dry_run,
        )
        while not self._stop_event.is_set():
            try:
                rescued = await self._scan_once()
                if rescued:
                    logger.info(
                        "goal_stuck_watchdog.tick_rescued", count=rescued
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("goal_stuck_watchdog.scan_iteration_crashed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.config.poll_interval_sec,
                )
            except asyncio.TimeoutError:
                pass
        logger.info("goal_stuck_watchdog.stopped")

    def start(self) -> asyncio.Task:
        """镜像 watcher.py:233-240 的 respawn-on-done 模式。
        self._task 为 None 或 .done() 时都可重新拉起（覆盖 silent death 场景）。
        """
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(
                self.loop(), name="goal-stuck-watchdog"
            )
        return self._task

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()


__all__ = [
    "GoalStuckWatchdog",
    "GoalStuckScanner",
    "GoalStuckWatchdogConfig",
]
