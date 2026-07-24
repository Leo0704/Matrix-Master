"""调度器执行器：把 ``tasks`` 行的 action 派发到设备适配器。

实现 :class:`matrix.scheduler.scheduler.TaskExecutor` Protocol。

派发表：
- ``device_publish`` → ``device_publisher.publish(...)``
- ``device_like`` / ``device_comment`` / ``device_collect`` / ``device_follow`` /
  ``device_interact`` → ``device_interactor.interact(...)``
- ``device_collect_metrics`` → ``device_collector.collect(...)`` →
  写入 ``note_metrics`` + 更新 ``notes.collected_at``（Phase 1 P1-1 之前只返回 bool 丢指标）
- 其他 action / 缺少 device_interactor 时 → 返回 False

device_interactor 允许为 None（仅在只发不互动场景）；缺该依赖时互动类 action 一律 False。

Phase 1 P1-1：构造函数新增 ``session_factory``（写 note_metrics/note 用）和
``notifier``（发 note.collected / note.collect.failed 反馈）。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from matrix.monitoring.logging import get_logger

from matrix.scheduler.circuit_breaker import (
    CircuitBreaker,
    CircuitOpen,
    PerDeviceCircuitBreaker,
)
from matrix.scheduler.scheduler import TaskLike

logger = get_logger(__name__)


_INTERACT_ACTIONS = frozenset(
    {"device_like", "device_comment", "device_collect", "device_follow", "device_interact"}
)


class DeviceTaskExecutor:
    """调度器派发执行器：按 task.action 路由到对应 device 协议。"""

    def __init__(
        self,
        *,
        device_publisher: Any,
        device_collector: Any,
        device_interactor: Any | None = None,
        session_factory: Any | None = None,
        notifier: Any | None = None,
        breaker: Any | None = None,
    ) -> None:
        self._publisher = device_publisher
        self._collector = device_collector
        self._interactor = device_interactor
        # Phase 1：写 note_metrics/note 用；调用方可注入 None → 仅返回 bool 不持久化
        # （保留向后兼容，测试场景可省）
        self._session_factory = session_factory
        self._notifier = notifier
        # Phase 2a #7 → W3：按 device_id 分桶的熔断器（每台设备独立计数/冷却，
        # 不再进程级单例全设备连坐）。None → 不熔断（向后兼容 dev/test）。
        # 兼容旧的调用方（app.py）传入单个 CircuitBreaker：当作参数模板
        # 包装成 PerDeviceCircuitBreaker。
        if isinstance(breaker, CircuitBreaker):
            breaker = PerDeviceCircuitBreaker(
                window=breaker.window,
                threshold=breaker.threshold,
                cool_off=breaker.cool_off,
            )
        self._breaker: PerDeviceCircuitBreaker | None = breaker

    async def execute(self, task: TaskLike) -> bool:
        # W3：该设备熔断打开时不算任务失败——抛 CircuitOpen 让调度器把任务
        # 退回 pending 并推迟到冷却结束后再执行（不 mark_failed）。
        if self._breaker is not None and self._breaker.is_open(task.device_id):
            logger.warning(
                "executor.circuit_open", task_id=task.id, action=task.action
            )
            raise CircuitOpen(self._breaker.retry_after(task.device_id))

        action = task.action
        ok = False
        try:
            if action == "device_publish":
                ok = await self._do_publish(task)
            elif action == "device_collect_metrics":
                ok = await self._do_collect(task)
            elif action in _INTERACT_ACTIONS:
                ok = await self._do_interact(task, action)
            else:
                logger.warning(
                    "executor.unknown_action", action=action, task_id=task.id
                )
                ok = False
        except Exception as exc:
            # 兜底：适配器实现意外抛 → 走熔断记录 + 返 False，绝不让 worker 崩
            logger.exception(
                "executor.unhandled_exception", task_id=task.id, action=action
            )
            await self._record_failure(task)
            await self._notify_executor_failed(task, action, str(exc))
            return False

        if not ok:
            await self._record_failure(task)
        return ok

    # ---- 熔断辅助（W3：按设备计数，closed→open 边界才告警） ----------------

    async def _record_failure(self, task: TaskLike) -> None:
        """给该设备记一次失败；触发 closed→open 边界时每设备告警一次。"""
        if self._breaker is None:
            return
        if self._breaker.record_failure(task.device_id):
            await self._notify_circuit_open(task)

    # ---- action handlers ------------------------------------------------

    async def _do_publish(self, task: TaskLike) -> bool:
        payload = task.payload or {}
        result = await self._publisher.publish(
            device_id=task.device_id,
            account_id=task.account_id,
            title=payload.get("title", ""),
            content=payload.get("content", ""),
            images=list(payload.get("images") or []),
            tags=list(payload.get("tags") or []),
            request_id=task.request_id,
            timeout=120.0,
        )
        return bool(getattr(result, "ok", False))

    async def _do_collect(self, task: TaskLike) -> bool:
        """采集笔记表现数据 → 落 ``note_metrics`` + 更新 ``notes.collected_at``。

        Phase 1 之前：拿到 metrics dict 后 return bool，**指标全丢**。这导致 KPI 永远
        拿不到真实数据，DECIDING 阶段只能基于空值瞎判。

        Phase 1 修复：拿到 dict → 写 ``NoteMetric`` 时间序列行（PK 是 note_id+ts）→
        更新 ``Note.collected_at`` + ``collected_run_id`` → 通知 notifier。
        """
        payload = task.payload or {}
        platform_note_id = str(payload.get("platform_note_id") or "")
        try:
            metrics = await self._collector.collect(
                device_id=task.device_id,
                account_id=task.account_id,
                platform_note_id=platform_note_id,
                scope=str(payload.get("scope") or "recent_24h"),
            )
        except Exception:
            logger.exception("executor.collect_failed", task_id=task.id)
            await self._notify_collect_failed(
                task, platform_note_id, reason="device_collector_exception"
            )
            return False

        if not isinstance(metrics, dict):
            logger.warning(
                "executor.collect_non_dict",
                task_id=task.id,
                type=type(metrics).__name__,
            )
            await self._notify_collect_failed(
                task, platform_note_id, reason="collector_returned_non_dict"
            )
            return False

        # Phase 1：写库 + 更新 note
        note_id_str = payload.get("note_id")
        if self._session_factory is None or not note_id_str:
            # 没注入 session_factory 或缺 note_id → 仅返回 True 不持久化
            return True

        from uuid import UUID

        from matrix.db.models import Note, NoteMetric

        try:
            note_uuid = UUID(str(note_id_str))
        except (ValueError, TypeError):
            logger.warning("executor.collect_bad_note_id", note_id=note_id_str)
            return True

        ts = datetime.now(UTC)
        try:
            async with self._session_factory() as session:
                row = NoteMetric(
                    note_id=note_uuid,
                    ts=ts,
                    views=int(metrics.get("views", 0) or 0),
                    likes=int(metrics.get("likes", 0) or 0),
                    collects=int(metrics.get("collects", 0) or 0),
                    comments=int(metrics.get("comments", 0) or 0),
                    follows_gained=int(metrics.get("follows_gained", 0) or 0),
                )
                session.add(row)
                note = await session.get(Note, note_uuid)
                if note is not None:
                    note.collected_at = ts
                    run_id_str = payload.get("run_id")
                    if run_id_str:
                        try:
                            note.collected_run_id = UUID(str(run_id_str))
                        except (ValueError, TypeError):
                            note.collected_run_id = None
                await session.commit()
        except Exception:
            logger.exception("executor.collect_persist_failed", task_id=task.id)
            await self._notify_collect_failed(
                task, platform_note_id, reason="persist_exception"
            )
            return False

        # Phase 1 P1-1：通知"数据采集完成"
        await self._notify_collected(task, platform_note_id, metrics)

        # v0.7+ 时序修复：24h 真数据落表后，spawn 一条独立 ANALYZE run 做复盘。
        # 之前 ANALYZE 在主链里吃 COLLECT 的"发布即时数据"（≈0）写复盘——假数据
        # 直接污染 KB 经验卡；现在复盘在真数据落表后进行，AgentRunWorker 会认领。
        await self._spawn_analyze_run(task, note_uuid)
        return True

    async def _spawn_analyze_run(self, task: TaskLike, note_uuid: Any) -> None:
        """采集落表后 spawn 独立复盘 run（entry="ANALYZE"）。

        goal_id/run_id/note_id 都来自 collect task 的 payload（publish 入队时塞的）；
        business_id 优先取 goal、兜底取原 run；round_number 取原 run 的轮次。
        任何失败只记日志——数据已落表，复盘可手动补，不挡采集主流程。
        """
        payload = task.payload or {}
        goal_id_str = payload.get("goal_id")
        if not goal_id_str or self._session_factory is None:
            return
        try:
            from uuid import UUID as _UUID

            from matrix.db.models import AgentRun, Goal

            goal_uuid = _UUID(str(goal_id_str))
            business_id = None
            round_number = None
            async with self._session_factory() as session:
                goal = await session.get(Goal, goal_uuid)
                if goal is not None:
                    business_id = goal.business_id
                run_id_str = payload.get("run_id")
                if run_id_str:
                    try:
                        src_run = await session.get(AgentRun, _UUID(str(run_id_str)))
                    except (ValueError, TypeError):
                        src_run = None
                    if src_run is not None:
                        round_number = src_run.round_number
                        if business_id is None:
                            business_id = src_run.business_id

            from matrix.agent.run_manager import create_run as _agent_create_run

            await _agent_create_run(
                goal_id=goal_uuid,
                goal_type="analyze",
                entry="ANALYZE",
                note_id=note_uuid,
                business_id=business_id,
                round_number=round_number,
            )
            logger.info(
                "executor.analyze_run_spawned",
                note_id=str(note_uuid),
                goal_id=str(goal_uuid),
            )
        except Exception:
            logger.exception(
                "executor.spawn_analyze_run failed", note_id=str(note_uuid)
            )

    async def _do_interact(self, task: TaskLike, action: str) -> bool:
        if self._interactor is None:
            logger.warning("executor.no_interactor", action=action, task_id=task.id)
            return False
        payload = task.payload or {}
        # DeviceInteractor Protocol 的 action 域是 'like' | 'comment'，去掉 'device_' 前缀
        interact_action = action.removeprefix("device_")
        result = await self._interactor.interact(
            device_id=task.device_id,
            account_id=task.account_id,
            action=interact_action,
            target_note_id=str(payload.get("target_note_id") or ""),
            content=payload.get("content"),
            request_id=task.request_id,
            timeout=60.0,
        )
        return bool(getattr(result, "ok", False))

    # ---- notifier helpers (Phase 1 P1-1) --------------------------------

    async def _notify_collected(
        self, task: TaskLike, platform_note_id: str, metrics: dict[str, Any]
    ) -> None:
        if self._notifier is None:
            return
        payload = task.payload or {}
        short_id = platform_note_id[:8] if platform_note_id else "?"
        try:
            await self._notifier(
                "note.collected",
                {
                    "note_id": payload.get("note_id", ""),
                    "goal_id": payload.get("goal_id", ""),
                    "run_id": payload.get("run_id", ""),
                    "business_id": payload.get("business_id", ""),
                    "device_id": str(task.device_id) if task.device_id else "",
                    "platform_note_id": platform_note_id,
                    "short_id": short_id,
                    "views": int(metrics.get("views", 0) or 0),
                    "likes": int(metrics.get("likes", 0) or 0),
                    "collects": int(metrics.get("collects", 0) or 0),
                    "comments": int(metrics.get("comments", 0) or 0),
                    "follows_gained": int(metrics.get("follows_gained", 0) or 0),
                },
            )
        except Exception:
            logger.exception("executor.notify_collected_failed", task_id=task.id)

    async def _notify_collect_failed(
        self, task: TaskLike, platform_note_id: str, *, reason: str
    ) -> None:
        if self._notifier is None:
            return
        payload = task.payload or {}
        short_id = platform_note_id[:8] if platform_note_id else "?"
        try:
            await self._notifier(
                "note.collect.failed",
                {
                    "note_id": payload.get("note_id", ""),
                    "goal_id": payload.get("goal_id", ""),
                    "run_id": payload.get("run_id", ""),
                    "business_id": payload.get("business_id", ""),
                    "device_id": str(task.device_id) if task.device_id else "",
                    "platform_note_id": platform_note_id,
                    "short_id": short_id,
                    "reason": reason,
                },
            )
        except Exception:
            logger.exception("executor.notify_collect_failed_failed", task_id=task.id)

    # ---- Phase 2a #7：熔断器相关通知 ----

    async def _notify_circuit_open(self, task: TaskLike) -> None:
        """熔断 closed→open 边界告警（每设备每次打开只发一条，不刷屏）。"""
        if self._notifier is None:
            return
        # 用 device_id 作为 subject；不带 goal/run（熔断是全局的，不是某条 task）
        payload = task.payload or {}
        try:
            await self._notifier(
                "agent.alert",
                {
                    "code": "CIRCUIT_OPEN",
                    "severity": "warning",
                    "title": "设备任务熔断中",
                    "body": "近期失败率过高，调度器暂停派发新任务，等冷却结束再恢复。",
                    "business_id": payload.get("business_id", ""),
                    "device_id": str(task.device_id) if task.device_id else "",
                    "action": task.action,
                },
            )
        except Exception:
            logger.exception("executor.notify_circuit_open_failed", task_id=task.id)

    async def _notify_executor_failed(
        self, task: TaskLike, action: str, error: str
    ) -> None:
        """兜底异常通知：适配器自己抛时也能让运营看到。"""
        if self._notifier is None:
            return
        payload = task.payload or {}
        try:
            await self._notifier(
                "agent.alert",
                {
                    "code": "EXECUTOR_EXCEPTION",
                    "severity": "error",
                    "title": "设备任务执行异常",
                    "body": f"action={action}: {error[:200]}",
                    "note_id": payload.get("note_id", ""),
                    "goal_id": payload.get("goal_id", ""),
                    "run_id": payload.get("run_id", ""),
                    "business_id": payload.get("business_id", ""),
                    "device_id": str(task.device_id) if task.device_id else "",
                    "action": action,
                },
            )
        except Exception:
            logger.exception(
                "executor.notify_executor_failed_failed", task_id=task.id
            )
