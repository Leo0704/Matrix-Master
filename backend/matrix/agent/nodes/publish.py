"""PUBLISH 节点：阻塞等 APK 发布回报。

v0.7+ 行为变更（修 #4 stagger 形同虚设）：
- 读 ``state["slot"]["scheduled_at"]``，如果晚于当前时间则 ``asyncio.sleep`` 到点再发布
- 修完后：5 台设备排 9:00/9:15/9:30/9:45/10:00 就真的按这个时间发，不再一股脑全在 9:00 发

返回 ``{ok, platform_note_id, platform_url, error_*}``，guard 依此决定
转移方向。

v0.7 Phase 5：成功后通过 ``note_writer`` 把对应 notes 行更新成
``status='published'`` + 绑 account_id / 平台回执。失败时不动 notes。

Phase 1 P1-1：成功后多干三件事：
1) note_writer 一次 upsert 里带上 ``scheduled_collect_at = now + 24h``
   （前端展示用；schema 在 f1e2d3c4b5a6 迁移里加列）
2) 调 ``task_writer`` 入队一条 ``device_collect_metrics`` task，
   ``scheduled_at = now + 24h``，由调度器到点执行
3) 调 notifier 发 ``note.published``
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from matrix.monitoring.logging import get_logger

from .._services import get_services
from ..types import AgentState

logger = get_logger(__name__)


# Phase 1 P1-1：发布成功后等多久采集表现数据。
# 硬编码常量；用户没要求 per-goal 可配，阶段 2 再说。
COLLECT_DELAY = timedelta(hours=24)


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
                scheduled_at_str=scheduled_at_str,
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
            now = datetime.now(UTC)
            collect_at = now + COLLECT_DELAY
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
                        "published_at": now,
                        # Phase 1：写入 scheduled_collect_at 让前端展示"X 小时后采集"
                        "scheduled_collect_at": collect_at,
                    }
                )
            except Exception:
                logger.exception("publish.note_writer failed")
            else:
                # Phase 1 P1-1：入队 24h 后的 device_collect_metrics task。
                # note_writer 成功后才做；失败不挡主流程（collect 是另一回事）。
                task_writer = getattr(services, "task_writer", None)
                platform_note_id = publish_result.get("platform_note_id")
                if task_writer is not None and platform_note_id:
                    try:
                        await _enqueue_collect_task(
                            task_writer=task_writer,
                            note_id=note_id,
                            goal_id=state.get("goal_id"),
                            run_id=state.get("run_id"),
                            device_id=_as_uuid(slot.get("device_id")),
                            account_id=_as_uuid(slot.get("account_id")),
                            platform_note_id=str(platform_note_id),
                            collect_at=collect_at,
                        )
                    except Exception:
                        logger.exception("publish.task_writer failed")
                else:
                    logger.warning(
                        "publish.skip_enqueue_collect",
                        has_task_writer=task_writer is not None,
                        has_platform_note_id=bool(platform_note_id),
                    )

                # Phase 1 P1-1：通知"笔记已发布"
                try:
                    await services.notifier(
                        "note.published",
                        {
                            "goal_id": state.get("goal_id"),
                            "run_id": state.get("run_id"),
                            "note_id": str(note_id),
                            "title": str(draft.get("title", ""))[:30],
                        },
                    )
                except Exception:
                    logger.exception("publish.notifier failed")

    return {
        "publish_result": publish_result,
        "last_error": None if publish_result["ok"] else {
            "code": publish_result.get("error_code") or "PUBLISH_FAILED",
            "message": publish_result.get("error_message") or "",
        },
    }


async def _enqueue_collect_task(
    *,
    task_writer: Any,
    note_id: UUID,
    goal_id: Any,
    run_id: Any,
    device_id: UUID | None,
    account_id: UUID | None,
    platform_note_id: str,
    collect_at: datetime,
) -> None:
    """入队一条 24h 后的 device_collect_metrics task。

    ``task_writer`` 必填字段（``scheduler/db.py:97``）：
    plan_id / device_id / account_id / action / payload / request_id / scheduled_at。
    plan_id 复用 goal 维度唯一的 post_publish_collect plan，避免每条笔记一条 plan 行。
    """
    from matrix.db.session import get_session_factory
    from ._ensure_collect_plan import ensure_collect_plan_id

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan_id = await ensure_collect_plan_id(session, goal_id)

    await task_writer(
        {
            "id": uuid.uuid4(),
            "plan_id": plan_id,
            "device_id": device_id,
            "account_id": account_id,
            "action": "device_collect_metrics",
            "payload": {
                "platform_note_id": platform_note_id,
                "scope": "recent_24h",
                "note_id": str(note_id),
                "goal_id": str(goal_id) if goal_id else "",
                "run_id": str(run_id) if run_id else "",
            },
            "request_id": f"collect-{note_id}-{int(datetime.now(UTC).timestamp())}",
            "scheduled_at": collect_at,
        }
    )


def _as_uuid(value):
    if isinstance(value, UUID):
        return value
    if value is None:
        return None
    return UUID(str(value))