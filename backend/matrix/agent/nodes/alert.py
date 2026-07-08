"""ALERT 节点：通知运营者 + 隔离风险 + 等待人工确认。"""

from __future__ import annotations

import logging
from typing import Any

from .._services import get_services
from ..types import AgentState

logger = logging.getLogger(__name__)


_ALERT_CODES = {
    "KB_RETRIEVE_FAILED": "knowledge base unreachable",
    "LLM_FAILED": "LLM provider failure",
    "DRAFT_LLM_FAILED": "draft generation failed",
    "REVISE_LLM_FAILED": "revision failed",
    "PUBLISH_FAILED": "platform publish failed",
    "RISK_BLOCKED": "platform risk control",
    "DEVICE_OFFLINE": "device offline",
    "OUT_OF_ACTIVE_WINDOW": "out of active posting window",
}


async def alert_node(state: AgentState) -> dict[str, Any]:
    """向 ``Notifier`` 发一次通知；等待人工 / RunManager 确认后再回 IDLE。

    Returns：``{"alert": {...}, "last_error": ...}``
    """
    services = get_services()
    err = state.get("last_error") or {}
    code = str(err.get("code", "UNKNOWN"))
    message = str(err.get("message", ""))

    severity = _severity_for(code)
    alert_payload = {
        "code": code,
        "message": message,
        "description": _ALERT_CODES.get(code, "uncategorized"),
        "run_id": str(state.get("run_id", "")),
        "severity": severity,
        "state_before": state.get("current_state") or "UNKNOWN",
    }

    try:
        await services.notifier(
            "agent.alert",
            alert_payload,
        )
    except Exception:
        # 通知失败也要继续，不让 state machine 永远停在 ALERT
        logger.exception("alert.notifier failed")

    # 等待人工 / RunManager 注入 ack；不自动 ack
    return {
        "alert": alert_payload,
        # 把 last_error 清掉，避免 ALERT 下游 guard 误判
        "last_error": None,
    }


def _severity_for(code: str) -> int:
    high = {
        "RISK_BLOCKED",
        "DEVICE_OFFLINE",
        "PUBLISH_FAILED",
    }
    if code in high:
        return 3
    return 2
