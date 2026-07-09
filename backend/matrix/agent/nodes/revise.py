"""REVISE 节点：按失败原因改写 + 累加 attempts。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any

from .._services import get_services, llm_complete
from ..types import AgentState, ReviewFailureReason
from ._util import parse_json_response

logger = get_logger(__name__)


REVISE_INSTRUCTIONS = {
    ReviewFailureReason.FORBIDDEN_WORD.value: (
        "请替换以下违禁词（用同义替换并保持语义）: {hits}。"
        "重写时再次避免使用同样表达，并保持文风一致。"
    ),
    ReviewFailureReason.DUPLICATE.value: (
        "当前笔记与历史爆款相似度过高。请换一个切入角度、"
        "更换开头钩子和例子，并保留核心选题。"
    ),
    ReviewFailureReason.LOW_HUMAN_SCORE.value: (
        "上一稿 AI 痕迹明显。请减少模板句（'首先''其次''总之'），"
        "加入口语词、第一人称感受、个人案例；句长更不规律。"
    ),
}


async def revise_node(state: AgentState) -> dict[str, Any]:
    """读 last review 原因，调 LLM 改写并写回 draft。

    完成后 ``revise_attempts`` +1；DRAFT 节点会根据 attempts 决定提示模板。
    """
    get_services()  # 触发 service 初始化（若未设置）
    draft = state.get("draft") or {}
    review = state.get("review") or {}
    attempts = int(state.get("revise_attempts", 0))

    reason_value = _first_reason(review)
    instruction = REVISE_INSTRUCTIONS.get(
        reason_value,
        "请基于失败原因改进，重写文案。",
    ).format(hits=",".join(review.get("forbidden_hits") or []))

    persona_chunk_text = ""
    rules_chunk_text = ""
    if state.get("research_chunks"):
        # 复用 RESEARCH 阶段拉到的人设/规则片段
        for c in state["research_chunks"]:
            t = getattr(c, "doc_type", "")
            if t == "persona" and not persona_chunk_text:
                persona_chunk_text = c.text
            elif t == "rule" and not rules_chunk_text:
                rules_chunk_text = c.text

    user_prompt = (
        f"原始标题: {draft.get('title','')}\n"
        f"原始正文: {draft.get('content','')}\n"
        f"原始 tags: {draft.get('tags') or []}\n\n"
        f"改写要求: {instruction}\n"
        f"人设参考: {persona_chunk_text or '(none)'}\n"
        f"规则参考: {rules_chunk_text or '(none)'}\n\n"
        "输出 JSON：{\"title\": str, \"content\": str, \"tags\": [str, ...]}"
    )

    new_draft = dict(draft)
    try:
        raw = await llm_complete(
            "你是小红书爆款文案写手，按要求严格改写，保持人设一致。",
            user_prompt,
        )
        parsed = parse_json_response(raw)
        if parsed.get("title"):
            new_draft["title"] = str(parsed["title"]).strip()
        if parsed.get("content"):
            new_draft["content"] = str(parsed["content"]).strip()
        if parsed.get("tags"):
            tags = parsed["tags"]
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            new_draft["tags"] = [str(t) for t in tags if t][:8]
    except Exception as exc:
        logger.exception("revise.llm failed")
        return {
            "draft": new_draft,
            "revise_attempts": attempts + 1,
            "last_error": {"code": "REVISE_LLM_FAILED", "message": str(exc)},
        }

    return {
        "draft": new_draft,
        "revise_attempts": attempts + 1,
        "last_error": None,
    }


def _first_reason(review: dict) -> str:
    """从 review.forbidden_hits / reason 提取第一个失败原因 key。"""
    hits = review.get("forbidden_hits") or []
    if hits:
        return ReviewFailureReason.FORBIDDEN_WORD.value
    reason = (review.get("reason") or "").lower()
    if "similarity" in reason or "duplicate" in reason:
        return ReviewFailureReason.DUPLICATE.value
    if "human" in reason or "ai-like" in reason or "拟人" in reason:
        return ReviewFailureReason.LOW_HUMAN_SCORE.value
    # 默认按最低分项判断
    score_human = float(review.get("score_human", 1.0))
    if score_human < 0.60:
        return ReviewFailureReason.LOW_HUMAN_SCORE.value
    return ReviewFailureReason.LOW_HUMAN_SCORE.value
