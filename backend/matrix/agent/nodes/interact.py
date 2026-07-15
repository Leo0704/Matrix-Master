"""INTERACT 节点（v0.6）：发后流量互推。

PUBLISH 成功后若有 ``interact_plan``，按 plan 逐条调用
``services.device_interactor.interact()``（走限速器 + 写 interactions 表）。
失败不中断，累加到 ``interact_results['failed']``。

plan 项形如 ``{"note_id": str, "kind": "like"|"comment", "content_template"?: str}``。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from .._services import get_services, llm_complete
from ..interact_policy import InteractPolicy
from ..nodes._util import parse_json_response
from ..prompts import INTERACT_SYSTEM, INTERACT_USER
from ..types import AgentState


@dataclass
class _TaskLike:
    """满足 scheduler.rate_limiter.TaskLike Protocol 的最小结构。"""

    action: str
    device_id: UUID
    account_id: UUID

logger = get_logger(__name__)


_VALID_KINDS = {"like", "comment"}


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


async def _gen_comment_text(
    *,
    note_title: str,
    note_content: str,
    persona_style: str,
    persona_tone: str,
    services,
) -> str | None:
    """调 LLM 生成评论文案。失败返回 None（节点不因 LLM 失败而崩）。"""
    try:
        user_prompt = INTERACT_USER.format(
            note_title=note_title[:80],
            note_content=(note_content or "")[:200],
            persona_style=persona_style or "(无)",
            persona_tone=persona_tone or "(无)",
        )
        raw = await llm_complete(
            INTERACT_SYSTEM, user_prompt, services=services, call_type="interact"
        )
        parsed = parse_json_response(raw)
        content = str(parsed.get("content", "")).strip()
        if not content:
            logger.warning("interact.llm.empty_content", raw=raw[:200])
            return None
        return content[:140]  # XHS 评论硬上限
    except Exception:
        logger.exception("interact.llm_failed")
        return None


async def interact_node(state: AgentState) -> dict[str, Any]:
    """按 plan 逐条做 like / comment，聚合 results。

    Returns:
        ``{"interact_results": {"succeeded": int, "failed": int, "details": [...]},
           "interact_attempts": int, "last_error": None | {"code": "PARTIAL_FAIL", ...}}``
    """
    services = get_services()
    plan = state.get("interact_plan") or []
    slot = state.get("slot") or {}
    device_id = _as_uuid(slot.get("device_id")) or uuid4()
    account_id = _as_uuid(slot.get("account_id")) or uuid4()

    # persona 上下文（用于 LLM 生成评论 + 限速 throttle）
    brief = state.get("brief") if isinstance(state.get("brief"), dict) else {}
    persona_style = str(brief.get("persona_style") or "")
    persona_tone = str(brief.get("persona_tone") or "")

    details: list[dict[str, Any]] = []
    succeeded = 0
    failed = 0

    if not plan:
        return {
            "interact_results": {
                "succeeded": 0,
                "failed": 0,
                "details": [],
            },
            "interact_attempts": int(state.get("interact_attempts", 0)),
            "last_error": None,
        }

    # Phase 2b #5 + A：构造去重 + 自适应策略（session_factory 缺失就跳过，
    # 等价于旧行为）
    policy: InteractPolicy | None = None
    if services.session_factory is not None:
        policy = InteractPolicy(services.session_factory)

    for item in plan:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).lower()
        target_note_id = str(item.get("note_id", "")).strip()
        if kind not in _VALID_KINDS or not target_note_id:
            failed += 1
            details.append(
                {
                    "kind": kind,
                    "target_note_id": target_note_id,
                    "ok": False,
                    "error_code": "INVALID_PLAN_ITEM",
                    "error_message": "kind must be like|comment and note_id non-empty",
                }
            )
            continue

        # Phase 2b：去重 + 风险自适应检查
        if policy is not None:
            decision = await policy.should_skip(
                account_id=account_id,
                target_note_id=target_note_id,
                kind=kind,
            )
            if decision.skip:
                # 跳过不算 failed（不是设备问题），但要记一条 details 让上层看见
                details.append(
                    {
                        "kind": kind,
                        "target_note_id": target_note_id,
                        "ok": False,
                        "error_code": decision.reason,
                        "error_message": decision.message,
                        "skipped": True,
                    }
                )
                continue

        # comment 必须先生成文案
        content: str | None = item.get("content")  # content_template 也走这条；运营者手填
        if kind == "comment" and not content:
            content = await _gen_comment_text(
                note_title=item.get("note_title", ""),
                note_content=item.get("note_content", ""),
                persona_style=persona_style,
                persona_tone=persona_tone,
                services=services,
            )
            if not content:
                failed += 1
                details.append(
                    {
                        "kind": kind,
                        "target_note_id": target_note_id,
                        "ok": False,
                        "error_code": "COMMENT_GEN_FAILED",
                        "error_message": "LLM did not return content",
                    }
                )
                continue

        # 限速检查（可选；rate_limiter=None 跳过）
        if services.rate_limiter is not None:
            task_like = _TaskLike(
                action="device_like" if kind == "like" else "device_comment",
                device_id=device_id,
                account_id=account_id,
            )
            decision = await services.rate_limiter.throttle(task_like)
            if not decision.ok:
                failed += 1
                details.append(
                    {
                        "kind": kind,
                        "target_note_id": target_note_id,
                        "ok": False,
                        "error_code": decision.reason,
                        "error_message": "rate_limiter throttled",
                    }
                )
                continue

        # 调设备
        request_id = str(uuid4())
        if services.device_interactor is None:
            failed += 1
            details.append(
                {
                    "kind": kind,
                    "target_note_id": target_note_id,
                    "ok": False,
                    "error_code": "NO_DEVICE_INTERACTOR",
                    "error_message": "device_interactor not configured",
                }
            )
            continue

        try:
            result = await services.device_interactor.interact(
                device_id=device_id,
                account_id=account_id,
                action=kind,
                target_note_id=target_note_id,
                content=content,
                request_id=request_id,
                timeout=60.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("interact.device_call_failed", kind=kind)
            failed += 1
            details.append(
                {
                    "kind": kind,
                    "target_note_id": target_note_id,
                    "ok": False,
                    "error_code": "DEVICE_RAISED",
                    "error_message": str(exc),
                }
            )
            continue

        if not result.ok:
            failed += 1
            details.append(
                {
                    "kind": kind,
                    "target_note_id": target_note_id,
                    "ok": False,
                    "error_code": result.error_code or "INTERACT_FAILED",
                    "error_message": result.error_message or "",
                }
            )
            continue

        # 成功：限速 record + 写 interactions 表
        if services.rate_limiter is not None:
            try:
                record_result = services.rate_limiter.record(task_like)
                if hasattr(record_result, "__await__"):
                    await record_result
            except Exception:
                logger.exception("interact.rate_record_failed")

        if services.interaction_writer is not None:
            try:
                await services.interaction_writer(
                    {
                        "account_id": account_id,
                        "target_note_id": target_note_id,
                        "type": kind,
                        "content": content,
                        "result": "success",
                        "request_id": request_id,
                    }
                )
            except Exception:
                # 写库失败不阻塞后续 plan
                logger.exception("interact.write_failed")

        succeeded += 1
        details.append(
            {
                "kind": kind,
                "target_note_id": target_note_id,
                "ok": True,
                "request_id": request_id,
            }
        )

    last_error: dict[str, Any] | None = None
    if failed > 0 and succeeded == 0:
        # 全部失败 → ALERT
        last_error = {
            "code": "INTERACT_ALL_FAILED",
            "message": f"{failed}/{len(plan)} interactions failed",
        }
    elif failed > 0:
        # 部分失败 → 不阻塞 COLLECT，但记 last_error 让上层感知
        last_error = {
            "code": "PARTIAL_FAIL",
            "message": f"{failed}/{len(plan)} interactions failed",
        }

    return {
        "interact_results": {
            "succeeded": succeeded,
            "failed": failed,
            "details": details,
        },
        "interact_attempts": int(state.get("interact_attempts", 0)) + 1,
        "last_error": last_error,
    }


__all__ = ["interact_node"]
