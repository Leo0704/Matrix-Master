"""状态机 guard 条件函数。

每个 ``can_*`` 函数读取 ``AgentState`` 返回 bool，
``StateMachine._route_*`` 根据返回值决定 next 节点。

guard 只读 state，不修改 state，也不做 IO。
所有判定阈值集中在 ``GuardConfig`` 中，便于调参与测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from .types import AgentState, ReviewFailureReason, State


@dataclass
class GuardConfig:
    """guard 阈值集合。"""

    revise_max_attempts: int = 3
    interact_max_attempts: int = 1  # v0.6 互动一般不重试；限速命中由 RateLimiter 负责

    # REVIEW 通过条件
    max_dup_score: float = 0.85          # 相似度 ≤ 该值才算不撞稿
    min_human_score: float = 0.60        # 拟人化 ≥ 该值才算通过

    # ANALYZE / IDLE
    require_note_metrics: bool = True    # ANALYZE 之前是否要求已回采

    # v0.6 互动节点
    enable_post_publish_interact: bool = True  # 总开关：关闭则 PUBLISH → COLLECT


class ReviewVerdict(TypedDict):
    """REVIEW guard 的诊断输出（给调度器/告警用）。"""

    passed: bool
    reasons: list[str]


# ---------------------------------------------------------------------------
# RESEARCH
# ---------------------------------------------------------------------------


def research_has_candidates(state: AgentState) -> bool:
    """RESEARCH 之后是否进入 DRAFT。"""
    return bool(state.get("candidates"))


def research_empty_to_alert(state: AgentState) -> bool:
    """RESEARCH 之后是否转 ALERT（无候选）。"""
    return not research_has_candidates(state)


# ---------------------------------------------------------------------------
# DRAFT → REVIEW (always ok unless no draft)
# ---------------------------------------------------------------------------


def review_verdict(state: AgentState, cfg: GuardConfig) -> ReviewVerdict:
    """REVIEW 的诊断结果（节点内部调用，也供 guard 测试）。"""
    review = state.get("review") or {}
    reasons: list[str] = []

    forbidden_hits = review.get("forbidden_hits") or []
    if forbidden_hits:
        reasons.append(ReviewFailureReason.FORBIDDEN_WORD.value)

    score_dup = float(review.get("score_dup", 0.0))
    if score_dup >= cfg.max_dup_score:
        reasons.append(ReviewFailureReason.DUPLICATE.value)

    score_human = float(review.get("score_human", 1.0))
    if score_human < cfg.min_human_score:
        reasons.append(ReviewFailureReason.LOW_HUMAN_SCORE.value)

    return ReviewVerdict(passed=not reasons, reasons=reasons)


def can_review_to_schedule(state: AgentState, cfg: GuardConfig) -> bool:
    """REVIEW → SCHEDULE：通过。"""
    verdict = review_verdict(state, cfg)
    return verdict["passed"]


def can_review_to_revise(state: AgentState, cfg: GuardConfig) -> bool:
    """REVIEW → REVISE：有失败原因，但未超过重试次数。"""
    if can_review_to_schedule(state, cfg):
        return False
    attempts = int(state.get("revise_attempts", 0))
    return attempts < cfg.revise_max_attempts


def can_review_to_alert(state: AgentState, cfg: GuardConfig) -> bool:
    """REVIEW → ALERT：有失败原因且超过重试次数。"""
    if can_review_to_schedule(state, cfg):
        return False
    attempts = int(state.get("revise_attempts", 0))
    return attempts >= cfg.revise_max_attempts


# ---------------------------------------------------------------------------
# REVISE → DRAFT / ALERT
# ---------------------------------------------------------------------------


def can_revise_to_draft(state: AgentState, cfg: GuardConfig) -> bool:
    attempts = int(state.get("revise_attempts", 0))
    return attempts < cfg.revise_max_attempts


def can_revise_to_alert(state: AgentState, cfg: GuardConfig) -> bool:
    attempts = int(state.get("revise_attempts", 0))
    return attempts >= cfg.revise_max_attempts


# ---------------------------------------------------------------------------
# SCHEDULE → DISPATCH
# ---------------------------------------------------------------------------


def schedule_has_slot(state: AgentState) -> bool:
    slot = state.get("slot")
    return bool(slot and slot.get("device_id") and slot.get("account_id"))


def schedule_no_slot_to_alert(state: AgentState) -> bool:
    return not schedule_has_slot(state)


# ---------------------------------------------------------------------------
# DISPATCH → PUBLISH
# ---------------------------------------------------------------------------


def dispatch_created_tasks(state: AgentState) -> bool:
    """DISPATCH 之后是否产生任务。"""
    task_ids = state.get("created_task_ids") or []
    return len(task_ids) > 0


def dispatch_no_task_to_alert(state: AgentState) -> bool:
    return not dispatch_created_tasks(state)


# ---------------------------------------------------------------------------
# PUBLISH → INTERACT / COLLECT / ALERT  （v0.6：发后流量互推）
# ---------------------------------------------------------------------------


def publish_succeeded(state: AgentState) -> bool:
    result: dict[str, Any] = state.get("publish_result") or {}
    return bool(result.get("ok"))


def publish_failed_to_alert(state: AgentState) -> bool:
    return not publish_succeeded(state)


def has_interact_plan(state: AgentState) -> bool:
    """PUBLISH 后是否有互动计划（且开关打开）。"""
    plan = state.get("interact_plan")
    return isinstance(plan, list) and len(plan) > 0


def can_publish_to_interact(state: AgentState, cfg: GuardConfig) -> bool:
    """PUBLISH → INTERACT：发布成功 + 有 plan + 开关打开。"""
    if not cfg.enable_post_publish_interact:
        return False
    return publish_succeeded(state) and has_interact_plan(state)


# ---------------------------------------------------------------------------
# IMAGE_GEN (v0.7 Phase 3)：fallback 决定路径
# ---------------------------------------------------------------------------


def image_gen_to_review(state: AgentState, cfg: GuardConfig) -> bool:
    """IMAGE_GEN → REVIEW：fallback=no_image（默认）或 draft.images 非空就放行。"""
    last_err = state.get("last_error") or {}
    if last_err.get("code", "").startswith("IMAGE_GEN_") and last_err.get("__force_alert"):
        return False
    draft = state.get("draft") or {}
    if draft.get("images"):
        return True
    # fallback=no_image 时 draft.images==[] 也放行（发纯文）
    return state.get("image_gen_fallback", "no_image") != "idle"


def route_after_image_gen(state: AgentState, cfg: GuardConfig) -> State:
    """IMAGE_GEN 的下个状态。"""
    if image_gen_to_review(state, cfg):
        return State.REVIEW
    return State.ALERT


# ---------------------------------------------------------------------------
# INTERACT → COLLECT / ALERT
# ---------------------------------------------------------------------------


def interact_has_results(state: AgentState) -> bool:
    """INTERACT 节点已写入 results（含 succeeded/failed 计数）。"""
    results = state.get("interact_results")
    return isinstance(results, dict) and "succeeded" in results


def interact_to_collect(state: AgentState, cfg: GuardConfig) -> bool:
    """INTERACT → COLLECT：只要 results 字段存在就放行（哪怕全失败）。"""
    return interact_has_results(state)


# ---------------------------------------------------------------------------
# COLLECT → ANALYZE / ALERT
# ---------------------------------------------------------------------------


def collect_has_metrics(state: AgentState, cfg: GuardConfig) -> bool:
    if not cfg.require_note_metrics:
        return True
    metrics = state.get("note_metrics") or {}
    return bool(metrics)


def collect_no_metrics_to_alert(state: AgentState, cfg: GuardConfig) -> bool:
    return not collect_has_metrics(state, cfg)


# ---------------------------------------------------------------------------
# ANALYZE → IDLE (always)
# ---------------------------------------------------------------------------


# ANALYZE 是一个收尾节点，本身转到 IDLE，不需要 guard。
# 但仍提供一个可测的 hook：
def analyze_complete(state: AgentState) -> bool:
    """ANALYZE 完成后是否回到 IDLE。

    策略：恒为 True（即使 ANALYZE 报错也回 IDLE，由人工/监控处理）。
    """
    return True


# ---------------------------------------------------------------------------
# ALERT → IDLE
# ---------------------------------------------------------------------------


def alert_acknowledged(state: AgentState) -> bool:
    """ALERT 之后人工 / 系统确认后再回 IDLE。

    state 上有 ``_alert_ack`` flag（运行时由 RunManager 注入），便于单元测试。
    """
    return bool(state.get("_alert_ack", False))


def alert_unacknowledged(state: AgentState) -> bool:
    return not alert_acknowledged(state)


# ---------------------------------------------------------------------------
# IDLE 出边
# ---------------------------------------------------------------------------


def idle_to_research(state: AgentState) -> bool:
    return str(state.get("entry", State.RESEARCH.value)).upper() != State.ANALYZE.value


def idle_to_analyze(state: AgentState) -> bool:
    return str(state.get("entry", State.RESEARCH.value)).upper() == State.ANALYZE.value


# ---------------------------------------------------------------------------
# Routing helpers（StateMachine 调用）
# ---------------------------------------------------------------------------


def route_after_research(state: AgentState, cfg: GuardConfig) -> State:
    if research_has_candidates(state):
        return State.DRAFT
    return State.ALERT


def route_after_review(state: AgentState, cfg: GuardConfig) -> State:
    if can_review_to_schedule(state, cfg):
        return State.SCHEDULE
    if can_review_to_alert(state, cfg):
        return State.ALERT
    return State.REVISE


def route_after_revise(state: AgentState, cfg: GuardConfig) -> State:
    if can_revise_to_alert(state, cfg):
        return State.ALERT
    return State.DRAFT


def route_after_schedule(state: AgentState, cfg: GuardConfig) -> State:
    return State.DISPATCH if schedule_has_slot(state) else State.ALERT


def route_after_dispatch(state: AgentState, cfg: GuardConfig) -> State:
    return State.PUBLISH if dispatch_created_tasks(state) else State.ALERT


def route_after_publish(state: AgentState, cfg: GuardConfig) -> State:
    if not publish_succeeded(state):
        return State.ALERT
    if can_publish_to_interact(state, cfg):
        return State.INTERACT
    # v0.7+ 时序修复：发布成功后直接回 IDLE 收工。
    # 之前去 COLLECT 拿"发布即时数据"（≈0）喂 ANALYZE 写复盘——假数据污染 KB；
    # 真复盘由 24h collect task 落表后触发的独立 ANALYZE run 完成。
    return State.IDLE


def route_after_interact(state: AgentState, cfg: GuardConfig) -> State:
    if interact_to_collect(state, cfg):
        # v0.7+：互动完成即收工（同 route_after_publish，不再去 COLLECT）
        return State.IDLE
    return State.ALERT


def route_after_collect(state: AgentState, cfg: GuardConfig) -> State:
    return State.ANALYZE if collect_has_metrics(state, cfg) else State.ALERT


def route_idle(state: AgentState, cfg: GuardConfig) -> State:
    return State.ANALYZE if idle_to_analyze(state) else State.RESEARCH


__all__ = [
    "GuardConfig",
    "ReviewVerdict",
    # Research
    "research_has_candidates",
    "research_empty_to_alert",
    # Review
    "review_verdict",
    "can_review_to_schedule",
    "can_review_to_revise",
    "can_review_to_alert",
    # Revise
    "can_revise_to_draft",
    "can_revise_to_alert",
    # Schedule
    "schedule_has_slot",
    "schedule_no_slot_to_alert",
    # Dispatch
    "dispatch_created_tasks",
    "dispatch_no_task_to_alert",
    # Publish
    "publish_succeeded",
    "publish_failed_to_alert",
    "has_interact_plan",
    "can_publish_to_interact",
    # Interact (v0.6)
    "interact_has_results",
    "interact_to_collect",
    # Collect
    "collect_has_metrics",
    "collect_no_metrics_to_alert",
    # Analyze / Alert / IDLE
    "analyze_complete",
    "alert_acknowledged",
    "alert_unacknowledged",
    "idle_to_research",
    "idle_to_analyze",
    # Routing
    "route_after_research",
    "route_after_review",
    "route_after_revise",
    "route_after_schedule",
    "route_after_dispatch",
    "route_after_publish",
    "route_after_interact",
    "route_after_collect",
    "route_idle",
]
