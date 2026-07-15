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
    ) -> None:
        self._publisher = device_publisher
        self._collector = device_collector
        self._interactor = device_interactor
        # Phase 1：写 note_metrics/note 用；调用方可注入 None → 仅返回 bool 不持久化
        # （保留向后兼容，测试场景可省）
        self._session_factory = session_factory
        self._notifier = notifier

    async def execute(self, task: TaskLike) -> bool:
        action = task.action

        if action == "device_publish":
            return await self._do_publish(task)

        if action == "device_collect_metrics":
            return await self._do_collect(task)

        if action in _INTERACT_ACTIONS:
            return await self._do_interact(task, action)

        logger.warning("executor.unknown_action", action=action, task_id=task.id)
        return False

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
        return True

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
                    "device_id": str(task.device_id) if task.device_id else "",
                    "platform_note_id": platform_note_id,
                    "short_id": short_id,
                    "reason": reason,
                },
            )
        except Exception:
            logger.exception("executor.notify_collect_failed_failed", task_id=task.id)
