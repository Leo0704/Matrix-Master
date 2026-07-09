"""SCHEDULE 节点：选时间窗 + 设备 + 账号。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from datetime import UTC, datetime
from typing import Any

from matrix.scheduler.active_window import is_in_active_window

from .._services import get_services
from ..protocols import ChosenSlot
from ..types import AgentState

logger = get_logger(__name__)


async def schedule_node(state: AgentState, *, now: datetime | None = None) -> dict[str, Any]:
    """按 persona 选活跃窗，并委派给 ``services.scheduler.choose_slot`` 选设备+账号。

    错误码：
    - ``OUT_OF_ACTIVE_WINDOW``：不在活跃窗内，待重试
    - ``NO_SCHEDULER``：未配置 scheduler（生产环境必须配）
    - ``NO_AVAILABLE_SLOT``：无候选 device/account 组合可用
    - ``SCHEDULE_FAILED``：``choose_slot`` 抛错

    Args:
        state: 当前 AgentState
        now: 注入的"当前时间"，测试时使用避开活跃窗外；默认 ``datetime.now(UTC)``
    """
    services = get_services()
    now = now or datetime.now(UTC)

    # 取 persona_config（活跃窗黑名单等）
    persona_config: dict | None = None
    try:
        # 集成层可以从 services 里取 persona rows；这里给个默认配置
        persona_config = services.system_metadata.get("persona_config")
    except Exception:
        persona_config = None

    if not is_in_active_window(now, persona_config):
        return {
            "scheduled_at": now.isoformat(),
            "slot": None,
            "last_error": {"code": "OUT_OF_ACTIVE_WINDOW", "message": "queue for retry"},
        }

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
        chosen = await services.scheduler.choose_slot(
            draft=state.get("draft") or {},
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
    }
    scheduled_at = (chosen.scheduled_at or now).isoformat()
    return {
        "scheduled_at": scheduled_at,
        "slot": slot,
        "last_error": None,
    }
