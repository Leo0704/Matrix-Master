"""PUBLISH 节点：把发布任务写进队列，等手机拉取执行并上报结果。

v0.7 Phase 6：改成 pull-based task delivery。
- 不再直接 `POST` 到手机 HTTP 服务；
- 写入一条 `action='device_publish'` 的 task；
- 阻塞轮询 task 状态，拿到手机 complete 结果后再更新 notes；
- 保留 dev/test fallback：当 `task_writer` 未配置时回退到直接 push。

Phase 1 P1-1 的后续动作（采集、复盘、通知）保持不变。
"""
from __future__ import annotations

import asyncio
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from matrix.db.models import Note, Task
from matrix.db.session import get_session_factory
from matrix.monitoring.logging import get_logger

from .._services import get_services
from ..types import AgentState

logger = get_logger(__name__)


# Phase 1 P1-1：发布成功后等多久采集表现数据。
COLLECT_DELAY = timedelta(hours=24)

# Phase 6：等手机 complete 的最长时间（秒）。
POLL_TIMEOUT_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 2.0

# 多设备错峰：把发布时间随机打散到 0~90 秒内，避免同一 WiFi/出口 IP 下批量发布。
PUBLISH_STAGGER_SECONDS = 90.0


async def publish_node(state: AgentState) -> dict[str, Any]:
    """按 scheduled_at 等待 → 写入 device_publish task → 轮询到结果。"""
    services = get_services()
    created_tasks = state.get("created_task_ids") or []
    if not created_tasks:
        return {
            "publish_result": {"ok": False, "error": "no_task"},
            "last_error": {"code": "NO_TASK", "message": "dispatch did not create task"},
        }

    draft = state.get("draft") or {}
    slot = state.get("slot") or {}

    # v0.7+ stagger：等 scheduled_at 到点再写任务
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
                # 睡醒后先确认 run 没被判死：watchdog 可能已在 sleep 期间把 run
                # 标成 timeout/cancelled，此时必须放弃发布，否则"已死 run 照样发笔记"
                if not await _run_still_running(state.get("run_id")):
                    logger.warning(
                        "publish.abort_run_not_running",
                        run_id=str(state.get("run_id")),
                    )
                    return {
                        "publish_result": {
                            "ok": False,
                            "error_code": "RUN_NOT_RUNNING",
                            "error_message": "run no longer running after schedule wait; abort publish",
                        },
                        "last_error": {
                            "code": "RUN_NOT_RUNNING",
                            "message": "run no longer running after schedule wait; abort publish",
                        },
                    }
        except (ValueError, TypeError) as exc:
            logger.warning(
                "publish.scheduled_at_parse_failed",
                scheduled_at_str=scheduled_at_str,
                error=str(exc),
            )

    task_writer = getattr(services, "task_writer", None)
    if task_writer is None:
        # dev/test fallback：直接推手机（旧行为）。
        return await _publish_direct(state, draft, slot, created_tasks[0])

    publish_result = await _publish_via_task_queue(state, draft, slot, created_tasks[0])

    # 兜底：无论成功失败，把 notes 状态同步一次（成功时 complete 接口已更新，
    # 这里再做一次幂等 upsert 保证 orchestrator 状态与 DB 一致）。
    await _sync_note_status(state, draft, slot, publish_result)

    return {
        "publish_result": publish_result,
        "last_error": None if publish_result["ok"] else {
            "code": publish_result.get("error_code") or "PUBLISH_FAILED",
            "message": publish_result.get("error_message") or "",
        },
    }


async def _publish_direct(
    state: AgentState, draft: dict, slot: dict, request_id: Any
) -> dict[str, Any]:
    """旧路径：task_writer 未配置时直接 push 到手机。"""
    services = get_services()
    try:
        result = await services.device_publisher.publish(
            device_id=_as_uuid(slot.get("device_id")),
            account_id=_as_uuid(slot.get("account_id")),
            title=str(draft.get("title", "")),
            content=str(draft.get("content", "")),
            images=list(draft.get("images") or []),
            tags=list(draft.get("tags") or []),
            request_id=str(request_id),
            timeout=120.0,
        )
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

    await _sync_note_status(state, draft, slot, publish_result)
    return {
        "publish_result": publish_result,
        "last_error": None if publish_result["ok"] else {
            "code": publish_result.get("error_code") or "PUBLISH_FAILED",
            "message": publish_result.get("error_message") or "",
        },
    }


async def _run_still_running(run_id: Any) -> bool:
    """查 ``agent_runs.status`` 是否仍 running。

    run_id 缺失 / 行不存在 / DB 异常时 fail-open 返回 True（保持旧行为，
    不因一次查询失败挡住发布）；只有明确查到非 running 才放弃。
    """
    if run_id is None:
        return True
    try:
        from matrix.db.models import AgentRun

        session_factory = get_session_factory()
        async with session_factory() as session:
            run = await session.get(AgentRun, _as_uuid(run_id))
            if run is None:
                return True
            return run.status == "running"
    except Exception:
        logger.exception("publish.run_status_check_failed", run_id=str(run_id))
        return True


async def _note_already_published(note_id: UUID) -> tuple[str | None, str | None] | None:
    """幂等检查：note 已 published 且有 platform_note_id → 返回平台回执，否则 None。

    DB 异常时返回 None（fail-open，走正常发布路径）。
    """
    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            note = await session.get(Note, note_id)
    except Exception:
        logger.exception("publish.already_published_check_failed", note_id=str(note_id))
        return None
    if note is not None and note.status == "published" and note.platform_note_id:
        return note.platform_note_id, note.platform_url
    return None


async def _find_open_publish_task(note_id: UUID) -> UUID | None:
    """查该 note 是否已有 pending/running 的 device_publish task（重启复用，不重复发）。

    DB 异常时返回 None（fail-open，按新建处理）。
    """
    from sqlalchemy import select

    try:
        session_factory = get_session_factory()
        async with session_factory() as session:
            stmt = (
                select(Task.id)
                .where(
                    Task.action == "device_publish",
                    Task.status.in_(("pending", "running")),
                    Task.payload["note_id"].astext == str(note_id),
                )
                .order_by(Task.created_at.desc())
                .limit(1)
            )
            return (await session.execute(stmt)).scalars().first()
    except Exception:
        logger.exception("publish.find_open_task_failed", note_id=str(note_id))
        return None


async def _publish_via_task_queue(
    state: AgentState, draft: dict, slot: dict, request_id: Any
) -> dict[str, Any]:
    """新路径：写 task 进队列，然后轮询等手机 complete。"""
    services = get_services()
    task_writer = services.task_writer
    assert task_writer is not None

    note_id = _as_uuid(draft.get("note_id"))
    device_id = _as_uuid(slot.get("device_id"))
    account_id = _as_uuid(slot.get("account_id"))
    goal_id = state.get("goal_id")

    # 幂等（重启/重复执行安全）：
    # 1) 该 note 已发布成功且有平台回执 → 直接成功返回，不再发
    if note_id is not None:
        published = await _note_already_published(note_id)
        if published is not None:
            logger.info("publish.skip_already_published", note_id=str(note_id))
            return {
                "ok": True,
                "platform_note_id": published[0],
                "platform_url": published[1],
                "error_code": None,
                "error_message": None,
                "note_id": str(note_id),
            }

    # 复用 goal 维度的 publish plan。
    from ._ensure_publish_plan import ensure_publish_plan_id

    session_factory = get_session_factory()
    async with session_factory() as session:
        plan_id = await ensure_publish_plan_id(session, goal_id)

    # 幂等 2)：已有该 note 的 pending/running device_publish task → 复用轮询，不新建
    task_id: UUID | None = None
    if note_id is not None:
        task_id = await _find_open_publish_task(note_id)
    if task_id is not None:
        logger.info(
            "publish.reuse_open_task",
            task_id=str(task_id),
            note_id=str(note_id),
        )
    else:
        task_id = uuid.uuid4()
        await task_writer(
            {
                "id": task_id,
                "plan_id": plan_id,
                "device_id": device_id,
                "account_id": account_id,
                "action": "device_publish",
                "payload": {
                    "title": str(draft.get("title", "")),
                    "content": str(draft.get("content", "")),
                    "images": list(draft.get("images") or []),
                    "tags": list(draft.get("tags") or []),
                    "note_id": str(note_id) if note_id else None,
                    "goal_id": str(goal_id) if goal_id else None,
                    "run_id": str(state.get("run_id")) if state.get("run_id") else None,
                    "visibility": "public",
                },
                "request_id": str(request_id),
                "scheduled_at": datetime.now(UTC) + timedelta(
                    seconds=random.uniform(0.0, PUBLISH_STAGGER_SECONDS)
                ),
            }
        )
        logger.info(
            "publish.task_enqueued",
            task_id=str(task_id),
            device_id=str(device_id),
            note_id=str(note_id) if note_id else None,
        )

    # 轮询 task 状态。
    deadline = asyncio.get_event_loop().time() + POLL_TIMEOUT_SECONDS
    while True:
        task = await _get_task(task_id)
        if task is None:
            return {
                "ok": False,
                "error_code": "TASK_NOT_FOUND",
                "error_message": "enqueued task disappeared",
                "platform_note_id": None,
                "platform_url": None,
                "note_id": str(note_id) if note_id else None,
            }
        if task.status == "success":
            # 读 notes 表拿平台回执（complete 接口已写入）。
            platform_note_id, platform_url = await _get_note_platform_info(note_id)
            return {
                "ok": True,
                "platform_note_id": platform_note_id,
                "platform_url": platform_url,
                "error_code": None,
                "error_message": None,
                "note_id": str(note_id) if note_id else None,
            }
        if task.status == "failed":
            last_error = task.last_error or {}
            return {
                "ok": False,
                "error_code": last_error.get("code") or "TASK_FAILED",
                "error_message": last_error.get("message") or "task failed on device",
                "platform_note_id": None,
                "platform_url": None,
                "note_id": str(note_id) if note_id else None,
            }
        if task.status in ("cancelled",):
            return {
                "ok": False,
                "error_code": "TASK_CANCELLED",
                "error_message": "task was cancelled",
                "platform_note_id": None,
                "platform_url": None,
                "note_id": str(note_id) if note_id else None,
            }

        if asyncio.get_event_loop().time() >= deadline:
            return {
                "ok": False,
                "error_code": "TASK_TIMEOUT",
                "error_message": f"device did not complete task within {POLL_TIMEOUT_SECONDS}s",
                "platform_note_id": None,
                "platform_url": None,
                "note_id": str(note_id) if note_id else None,
            }

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _get_task(task_id: UUID) -> Task | None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        return await session.get(Task, task_id)


async def _get_note_platform_info(note_id: UUID | None) -> tuple[str | None, str | None]:
    if note_id is None:
        return None, None
    session_factory = get_session_factory()
    async with session_factory() as session:
        note = await session.get(Note, note_id)
        if note is None:
            return None, None
        return note.platform_note_id, note.platform_url


async def _sync_note_status(
    state: AgentState, draft: dict, slot: dict, publish_result: dict
) -> None:
    """把 publish_result 同步回 notes 表，并触发采集/通知。"""
    services = get_services()
    note_id = _as_uuid(draft.get("note_id"))
    if note_id is None:
        return

    note_writer = getattr(services, "note_writer", None)
    if note_writer is None:
        return

    now = datetime.now(UTC)
    if publish_result["ok"]:
        collect_at = now + COLLECT_DELAY
        try:
            await note_writer(
                {
                    "id": note_id,
                    "account_id": _as_uuid(slot.get("account_id")),
                    "goal_id": state.get("goal_id"),
                    "run_id": state.get("run_id"),
                    "business_id": state.get("business_id"),
                    "title": str(draft.get("title", "")),
                    "content": str(draft.get("content", "")),
                    "images": list(draft.get("images") or []),
                    "tags": list(draft.get("tags") or []),
                    "status": "published",
                    "platform_note_id": publish_result.get("platform_note_id"),
                    "platform_url": publish_result.get("platform_url"),
                    "published_at": now,
                    "scheduled_collect_at": collect_at,
                }
            )
        except Exception:
            logger.exception("publish.note_writer failed")
            return

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
    else:
        try:
            await note_writer(
                {
                    "id": note_id,
                    "goal_id": state.get("goal_id"),
                    "run_id": state.get("run_id"),
                    "business_id": state.get("business_id"),
                    "status": "failed",
                }
            )
        except Exception:
            logger.exception("publish.note_writer.mark_failed failed")


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
    """入队一条 24h 后的 device_collect_metrics task。"""
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
