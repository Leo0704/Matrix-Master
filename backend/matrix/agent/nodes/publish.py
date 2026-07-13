"""PUBLISH 节点：阻塞等 APK 发布回报。

v0.7+ 行为变更（修 #4 stagger 形同虚设）：
- 读 ``state["slot"]["scheduled_at"]``，如果晚于当前时间则 ``asyncio.sleep`` 到点再发布
- 修完后：5 台设备排 9:00/9:15/9:30/9:45/10:00 就真的按这个时间发，不再一股脑全在 9:00 发

返回 ``{ok, platform_note_id, platform_url, error_*}``，guard 依此决定
转移方向。

v0.7 Phase 5：成功后通过 ``note_writer`` 把对应 notes 行更新成
``status='published'`` + 绑 account_id / 平台回执。失败时不动 notes。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from matrix.monitoring.logging import get_logger
from typing import Any
from uuid import UUID

from .._services import get_services
from ..types import AgentState

logger = get_logger(__name__)


async def publish_node(state: AgentState) -> dict[str, Any]:
    """按 scheduled_at 等待 → 调 ``device_publisher.publish``。

    v0.7+：如果 ``state["slot"]["scheduled_at"]`` 晚于当前时间，先 sleep 再发
    （实现 round-level stagger 错开）。v0.7 之前是无脑立即发。
    """
    services = get_services()
    created_tasks = state.get("created_task_ids") or []
    if not created_tasks:
        return {
            "publish_result": {"ok": False, "error": "no_task"},
            "last_error": {"code": "NO_TASK", "message": "dispatch did not create task"},
        }

    draft = state.get("draft") or {}
    slot = state.get("slot") or {}

    # v0.7+ stagger：等 scheduled_at 到点再发
    scheduled_at_str = slot.get("scheduled_at")
    if scheduled_at_str:
        try:
            scheduled_at = datetime.fromisoformat(scheduled_at_str.replace("Z", "+00:00"))
            now = datetime.now(UTC)
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=UTC)
            if scheduled_at > now:
                wait_sec = (scheduled_at - now).total_seconds()
                logger.info(
                    "publish.wait_for_schedule",
                    wait_sec=wait_sec,
                    scheduled_at=scheduled_at.isoformat(),
                )
                await asyncio.sleep(wait_sec)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "publish.scheduled_at_parse_failed",
                scheduled_at=scheduled_at_str,
                error=str(exc),
            )
            # 解析失败时退化为"立即发"（不挡流程）

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

    # 把发布结果写回 notes 表（DRAFT 阶段已写入 status='draft' 的行）
    if publish_result["ok"]:
        note_writer = getattr(services, "note_writer", None)
        note_id = _as_uuid(draft.get("note_id"))
        if note_writer is not None and note_id is not None:
            try:
                await note_writer(
                    {
                        "id": note_id,
                        "account_id": _as_uuid(slot.get("account_id")),
                        # v0.7+ 第 2 期：透传 goal_id/run_id；PUBLISH 阶段的 idempotent upsert
                        # 会更新已有的 DRAFT 行（DRAFT 已写过 goal_id/run_id，PUBLISH 这里再覆盖一遍保持一致）
                        "goal_id": state.get("goal_id"),
                        "run_id": state.get("run_id"),
                        "title": str(draft.get("title", "")),
                        "content": str(draft.get("content", "")),
                        "images": list(draft.get("images") or []),
                        "tags": list(draft.get("tags") or []),
                        "status": "published",
                        "platform_note_id": publish_result.get("platform_note_id"),
                        "platform_url": publish_result.get("platform_url"),
                        "published_at": datetime.now(UTC),
                    }
                )
            except Exception:
                logger.exception("publish.note_writer failed")

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
