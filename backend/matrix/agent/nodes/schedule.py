"""SCHEDULE 节点：选时间窗 + 设备 + 账号。

v0.7+ round-level 优先路径：当 ``state['preassigned_slot']`` 存在（orchestrator 在
``_prepare_round`` 已为该 run 预分配了 device/account/scheduled_at），本节点直接复用
并二次校验设备/账号是否仍 active（防 round_allocator 之后状态变化），
不再调 ``services.scheduler.choose_slot``。

错误码（统一为 5 个）：
- ``OUT_OF_ACTIVE_WINDOW``：不在活跃窗内，待重试
- ``NO_PREASSIGNED_SLOT_INVALID``：预分配 slot 解析/校验失败（payload 缺字段、UUID 非法、
  round_allocator 缺失、device/账号已 inactive）
- ``NO_SCHEDULER``：无 preassigned slot 且未配 scheduler
- ``NO_AVAILABLE_SLOT``：旧路径 ``choose_slot`` 返 None
- ``SCHEDULE_FAILED``：``choose_slot`` 抛错

Args:
    state: 当前 AgentState
    now: 注入的"当前时间"，测试时使用避开活跃窗外；默认 ``datetime.now(UTC)``
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from matrix.scheduler.active_window import is_in_active_window

from .._services import get_services
from ..protocols import ChosenSlot
from ..types import AgentState

logger = get_logger(__name__)


async def _validate_preassigned(
    state: AgentState, now: datetime
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """处理 ``state['preassigned_slot']``：校验后返回 (slot_dict, error)。

    任一情况返回 ``(None, last_error_dict)``：
    - round_allocator 未注入（preassigned 不应该出现，但兜底）
    - 二次校验失败（设备/账号已 inactive / 风险分高 / 暂停中）

    成功返回 ``(slot_dict, None)``，slot_dict 即 dispatch 用的 state["slot"] 形态。
    """
    preassigned = state.get("preassigned_slot")
    if not isinstance(preassigned, dict) or not preassigned:
        return None, None  # 非 preassigned 路径

    services = get_services()
    if services.round_allocator is None:
        return None, {
            "code": "NO_PREASSIGNED_SLOT_INVALID",
            "message": "preassigned slot present but round_allocator missing",
        }

    try:
        device_id = UUID(str(preassigned["device_id"]))
        account_id = UUID(str(preassigned["account_id"]))
    except (KeyError, ValueError) as exc:
        return None, {
            "code": "NO_PREASSIGNED_SLOT_INVALID",
            "message": f"preassigned slot missing/invalid id: {exc}",
        }

    # v0.7+ 业务归属：预分配时必须带业务（orchestrator._build_run_payload 写入），
    # 二次校验连业务一起查——否则多业务并存时 slot 归属不明，存在串号风险
    try:
        business_id = UUID(str(preassigned["business_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        return None, {
            "code": "NO_PREASSIGNED_SLOT_INVALID",
            "message": f"preassigned slot missing/invalid business_id: {exc}",
        }

    valid = await services.round_allocator.is_slot_valid(
        device_id=device_id,
        account_id=account_id,
        business_id=business_id,
        now=now,
    )
    if not valid:
        return None, {
            "code": "NO_AVAILABLE_SLOT",
            "message": "preassigned device/account no longer active",
        }

    slot_dict: dict[str, Any] = {
        "device_id": str(device_id),
        "account_id": str(account_id),
        "reason": preassigned.get("reason") or "round_allocator.match",
        "scheduled_at": preassigned.get("scheduled_at"),
        "style_hint": preassigned.get("style_hint"),
    }
    return slot_dict, None


async def schedule_node(state: AgentState, *, now: datetime | None = None) -> dict[str, Any]:
    """按 persona 选活跃窗，并优先用 preassigned_slot（v0.7+ round 扇出），否则调 choose_slot。"""
    services = get_services()
    now = now or datetime.now(UTC)

    # 取 persona_config（活跃窗黑名单等）：app_config 优先，system_metadata 兜底
    from matrix.agent._persona_config import load_persona_config

    persona_config = await load_persona_config(services)

    if not is_in_active_window(now, persona_config):
        return {
            "scheduled_at": now.isoformat(),
            "slot": None,
            "last_error": {"code": "OUT_OF_ACTIVE_WINDOW", "message": "queue for retry"},
        }

    # 路径 1：orchestrator 预分配 slot（v0.7+ round 扇出）
    slot_dict, validation_error = await _validate_preassigned(state, now)
    if validation_error is not None:
        return {
            "scheduled_at": now.isoformat(),
            "slot": None,
            "last_error": validation_error,
        }
    if slot_dict is not None:
        scheduled_at = slot_dict.get("scheduled_at") or now.isoformat()
        return {
            "scheduled_at": scheduled_at,
            "slot": slot_dict,
            "last_error": None,
        }

    # 路径 2：旧随机 choose_slot（单 run / 无 goal 场景）
    if services.scheduler is None:
        return {
            "scheduled_at": now.isoformat(),
            "slot": None,
            "last_error": {
                "code": "NO_SCHEDULER",
                "message": "scheduler slot picker not configured",
            },
        }

    try:
        # v0.7+ 业务隔离：降级路径也把业务带给 slot_picker（有则过滤，无则全池）
        draft_with_business = dict(state.get("draft") or {})
        if state.get("business_id"):
            draft_with_business.setdefault("business_id", state["business_id"])
        chosen = await services.scheduler.choose_slot(
            draft=draft_with_business,
            persona_config=persona_config,
            now=now,
        )
    except Exception as exc:
        logger.exception("schedule.choose_slot failed")
        return {
            "scheduled_at": now.isoformat(),
            "slot": None,
            "last_error": {"code": "SCHEDULE_FAILED", "message": str(exc)},
        }

    if chosen is None:
        return {
            "scheduled_at": now.isoformat(),
            "slot": None,
            "last_error": {
                "code": "NO_AVAILABLE_SLOT",
                "message": "no active device/account combination available",
            },
        }

    if not isinstance(chosen, ChosenSlot):
        # 兜底：上游实现返回了普通 DeviceSlot 时补一个 scheduled_at
        chosen = ChosenSlot(
            device_id=chosen.device_id,
            account_id=chosen.account_id,
            reason=chosen.reason,
            scheduled_at=now,
        )
    slot = {
        "device_id": str(chosen.device_id),
        "account_id": str(chosen.account_id),
        "reason": chosen.reason,
        "scheduled_at": chosen.scheduled_at.isoformat() if chosen.scheduled_at else None,
        "style_hint": chosen.style_hint,
    }
    scheduled_at = (chosen.scheduled_at or now).isoformat()
    return {
        "scheduled_at": scheduled_at,
        "slot": slot,
        "last_error": None,
    }
