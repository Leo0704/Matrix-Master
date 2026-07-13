"""DISPATCH 节点：把 schedule 选中的 (device, account, time) 落到 agent state。

v0.7+ 行为变更（修 #3 双发布）：
- 不再写 task_writer / tasks 表 — 之前这会让 Scheduler 也执行一次 device_publish，
  跟 PUBLISH 节点直接调 device_publisher.publish() 打架，导致同一条笔记发 2 次。
- 现在 PUBLISH 节点是 device_publish 的唯一执行点（拿到 preassigned slot 后直接发）；
  DISPATCH 只负责校验 slot + 生成 synthetic task_id（用于日志关联/notes 表写入追踪）。
- Scheduler 之后只处理 interact/collect 等其他 action，不碰 device_publish。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any
from uuid import UUID, uuid4

from .._services import get_services
from ..types import AgentState

logger = get_logger(__name__)


async def dispatch_node(state: AgentState) -> dict[str, Any]:
    """校验 slot + 返 synthetic task_id（不再调 task_writer）。

    真实持久化由 PUBLISH 节点直接把发布结果写回 notes 表；DISPATCH 不再写 tasks 表。
    """
    draft = state.get("draft") or {}
    slot = state.get("slot") or {}
    scheduled_at = state.get("scheduled_at")

    if not slot or not slot.get("device_id") or not slot.get("account_id"):
        return {
            "created_task_ids": [],
            "last_error": {"code": "NO_SLOT", "message": "schedule did not pick device/account"},
        }

    # 校验 slot 的 UUID 合法性（防 SCHEDULE 节点传脏数据下来）
    try:
        _as_uuid(slot["device_id"])
        _as_uuid(slot["account_id"])
    except (ValueError, TypeError) as exc:
        return {
            "created_task_ids": [],
            "last_error": {"code": "NO_SLOT", "message": f"invalid slot id: {exc}"},
        }

    # synthetic task_id：用于 logs 关联、notes 表追踪。
    # 真实执行由 PUBLISH 节点直接调 device_publisher.publish() 完成（修 #3）。
    synthetic_task_id = uuid4()
    logger.info(
        "dispatch.handoff",
        task_id=str(synthetic_task_id),
        device_id=str(slot.get("device_id")),
        account_id=str(slot.get("account_id")),
        scheduled_at=scheduled_at,
    )

    return {
        "created_task_ids": [str(synthetic_task_id)],
        "last_error": None,
    }


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
