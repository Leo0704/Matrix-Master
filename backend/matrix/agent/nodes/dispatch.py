"""DISPATCH 节点：基于 schedule 选中设备/账号创建 task。"""

from __future__ import annotations

import logging
import uuid as uuidlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from .._services import get_services
from ..types import AgentState

logger = logging.getLogger(__name__)


async def dispatch_node(state: AgentState) -> dict[str, Any]:
    """写 1 个 publish task + 若干 interact task 候选（默认仅 1 个 publish）。

    真实持久化由 ``services.task_writer`` 完成；测试可注入 mock writer。
    """
    services = get_services()
    draft = state.get("draft") or {}
    slot = state.get("slot") or {}
    scheduled_at = state.get("scheduled_at")

    if not slot or not slot.get("device_id") or not slot.get("account_id"):
        return {
            "created_task_ids": [],
            "last_error": {"code": "NO_SLOT", "message": "schedule did not pick device/account"},
        }

    device_id = _as_uuid(slot["device_id"])
    account_id = _as_uuid(slot["account_id"])
    note_id = _as_uuid(draft.get("note_id") or uuid4())

    publish_payload = {
        "note_id": str(note_id),
        "title": draft.get("title", ""),
        "content": draft.get("content", ""),
        "images": list(draft.get("images") or []),
        "tags": list(draft.get("tags") or []),
    }

    task_records: list[dict[str, Any]] = [
        {
            "id": uuid4(),
            "action": "device_publish",
            "payload": publish_payload,
            "device_id": device_id,
            "account_id": account_id,
            "request_id": str(uuidlib.uuid4()),
            "scheduled_at": scheduled_at or datetime.now(UTC).isoformat(),
        },
    ]

    # 持久化（如 writer 注入）
    if services.task_writer is not None:
        try:
            for rec in task_records:
                await services.task_writer(rec)
        except Exception as exc:
            logger.exception("dispatch.task_writer failed")
            return {
                "created_task_ids": [],
                "last_error": {"code": "TASK_WRITE_FAILED", "message": str(exc)},
            }

    return {
        "created_task_ids": [str(r["id"]) for r in task_records],
        "last_error": None,
    }


def _as_uuid(value: Any) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
