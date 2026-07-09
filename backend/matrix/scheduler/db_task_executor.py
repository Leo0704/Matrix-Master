"""调度器执行器：把 ``tasks`` 行的 action 派发到设备适配器。

实现 :class:`matrix.scheduler.scheduler.TaskExecutor` Protocol。

派发表：
- ``device_publish`` → ``device_publisher.publish(...)``
- ``device_like`` / ``device_comment`` / ``device_collect`` / ``device_follow`` /
  ``device_interact`` → ``device_interactor.interact(...)``
- ``device_collect_metrics`` → ``device_collector.collect(...)``
- 其他 action / 缺少 device_interactor 时 → 返回 False

device_interactor 允许为 None（仅在只发不互动场景）；缺该依赖时互动类 action 一律 False。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any

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
    ) -> None:
        self._publisher = device_publisher
        self._collector = device_collector
        self._interactor = device_interactor

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
        payload = task.payload or {}
        try:
            metrics = await self._collector.collect(
                device_id=task.device_id,
                account_id=task.account_id,
                platform_note_id=str(payload.get("platform_note_id") or ""),
                scope=str(payload.get("scope") or "recent_24h"),
            )
        except Exception:
            logger.exception("executor.collect_failed", task_id=task.id)
            return False
        return isinstance(metrics, dict)

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
