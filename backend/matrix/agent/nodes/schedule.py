"""SCHEDULE 节点：选时间窗 + 设备 + 账号。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from matrix.scheduler.active_window import is_in_active_window

from .._services import get_services
from ..protocols import DeviceSlot
from ..types import AgentState

logger = get_logger(__name__)


async def schedule_node(state: AgentState) -> dict[str, Any]:
    """可选：根据 persona 选活跃窗，并委派给 ``services.scheduler`` 选设备+账号。

    单元测试可注入 ``services.scheduler`` 来 mock 选设备；未注入则用一个
    临时 UUID 作为占位 slot（标记 reason="synthetic_slot"），便于在没有
    scheduler 模块的情况下端到端流转。
    """
    services = get_services()
    now = datetime.now(UTC)

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
        # 占位：返回伪 slot，由 dispatch 阶段产生 synthetic task 记录
        slot = DeviceSlot(
            device_id=uuid4(),
            account_id=uuid4(),
            reason="synthetic_slot",
        )
        return {
            "scheduled_at": now.isoformat(),
            "slot": slot.__dict__,
            "last_error": None,
        }

    # 实际生产路径
    try:
        chosen = await services.scheduler.choose_slot(  # type: ignore[attr-defined]
            draft=state.get("draft") or {},
        )
        slot = DeviceSlot(
            device_id=chosen.device_id,
            account_id=chosen.account_id,
            reason="scheduler.choose_slot",
        )
        scheduled_at = chosen.scheduled_at or now
        return {
            "scheduled_at": scheduled_at.isoformat(),
            "slot": slot.__dict__,
            "last_error": None,
        }
    except Exception as exc:
        logger.exception("schedule.choose_slot failed")
        return {
            "scheduled_at": now.isoformat(),
            "slot": None,
            "last_error": {"code": "SCHEDULE_FAILED", "message": str(exc)},
        }
