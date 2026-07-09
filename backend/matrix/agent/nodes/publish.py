"""PUBLISH 节点：阻塞等 APK 发布回报。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any
from uuid import UUID

from .._services import get_services
from ..types import AgentState

logger = get_logger(__name__)


async def publish_node(state: AgentState) -> dict[str, Any]:
    """取第一个 task，调 ``device_publisher.publish``。

    返回 ``{ok, platform_note_id, platform_url, error_*}``，guard 依此决定
    转移方向。
    """
    services = get_services()
    created_tasks = state.get("created_task_ids") or []
    if not created_tasks:
        return {
            "publish_result": {"ok": False, "error": "no_task"},
            "last_error": {"code": "NO_TASK", "message": "dispatch did not create task"},
        }

    # 假设 store 在 payload 里；publish node 直接调一次 device_publisher
    draft = state.get("draft") or {}
    slot = state.get("slot") or {}
    try:
        result = await services.device_publisher.publish(
            device_id=_as_uuid(slot.get("device_id")),
            account_id=_as_uuid(slot.get("account_id")),
            title=str(draft.get("title", "")),
            content=str(draft.get("content", "")),
            images=list(draft.get("images") or []),
            tags=list(draft.get("tags") or []),
            request_id=str(created_tasks[0]),
            timeout=120.0,
        )
        # PublishResult → dict（langgraph state 必须 JSON 友好）
        publish_result = {
            "ok": result.ok,
            "platform_note_id": result.platform_note_id,
            "platform_url": result.platform_url,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "note_id": str(result.note_id),
        }
    except Exception as exc:
        logger.exception("publish.device_publisher raised")
        publish_result = {
            "ok": False,
            "error_code": "PUBLISH_RAISED",
            "error_message": str(exc),
        }

    return {
        "publish_result": publish_result,
        "last_error": None if publish_result["ok"] else {
            "code": publish_result.get("error_code") or "PUBLISH_FAILED",
            "message": publish_result.get("error_message") or "",
        },
    }


def _as_uuid(value):
    if isinstance(value, UUID):
        return value
    if value is None:
        return None
    return UUID(str(value))
