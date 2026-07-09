"""COLLECT 节点：发布 24h 后从 device 拉 metrics 写 note_metrics（占位）。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any
from uuid import UUID

from .._services import get_services
from ..types import AgentState

logger = get_logger(__name__)


async def collect_node(state: AgentState) -> dict[str, Any]:
    """等 24h 后由调度器触发（或测试时可立即返回 mock metrics）。

    这里直接调 ``device_collector.collect`` 一次，不真的延时——24h 是
    调度器/RunManager 的职责，状态机不做阻塞 sleep。
    """
    services = get_services()
    publish = state.get("publish_result") or {}
    slot = state.get("slot") or {}

    platform_note_id = publish.get("platform_note_id")
    if not platform_note_id:
        return {
            "note_metrics": {},
            "last_error": {"code": "NO_PLATFORM_NOTE_ID", "message": "publish did not return id"},
        }

    try:
        metrics = await services.device_collector.collect(
            device_id=_as_uuid(slot.get("device_id")),
            account_id=_as_uuid(slot.get("account_id")),
            platform_note_id=str(platform_note_id),
            scope="recent_24h",
        )
    except Exception as exc:
        logger.exception("collect.device_collector raised")
        return {
            "note_metrics": {},
            "last_error": {"code": "COLLECT_FAILED", "message": str(exc)},
        }

    # 保留只有合法 keys
    clean_metrics = {
        k: int(v)
        for k, v in metrics.items()
        if k in {"views", "likes", "collects", "comments", "follows_gained"}
    }

    return {
        "note_metrics": clean_metrics,
        "last_error": None,
    }


def _as_uuid(value):
    if isinstance(value, UUID):
        return value
    if value is None:
        return None
    return UUID(str(value))
