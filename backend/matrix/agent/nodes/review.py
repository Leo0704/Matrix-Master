"""REVIEW 节点：违禁词 + 去重 + 拟人化评分。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any

from .. import prompts
from .._services import get_services, llm_complete
from ..protocols import RetrieveQuery
from ..types import AgentState
from ._util import format_brief, join_chunks, parse_json_response

logger = get_logger(__name__)


async def review_node(state: AgentState) -> dict[str, Any]:
    """打分 + 通过/失败。结果回写 ``state["review"]``。"""
    services = get_services()
    draft = state.get("draft")
    if not draft:
        return {
            "review": {"passed": False, "reason": "no_draft"},
            "last_error": {"code": "NO_DRAFT", "message": "draft missing"},
        }
    title = draft.get("title", "")
    content = draft.get("content", "")

    # 1. 拿禁词 + 相似历史
    forbidden_words: list[str] = []
    similar_chunks: list = []
    try:
        rule_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=title + "\n" + content[:200], doc_types=("rule",), top_k=5)
        )
        for c in rule_chunks:
            text = c.text or ""
            if "[禁]" in text or "[forbidden]" in text.lower():
                word = text.split(":", 1)[-1].strip()
                # 去掉 [forbidden] 前缀
                if word.lower().startswith("[forbidden]") or word.lower().startswith("[禁]"):
                    word = word.replace("[forbidden]", "").replace("[禁]", "").strip()
                if word:
                    forbidden_words.append(word)
        similar_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=title + "\n" + content, doc_types=("history",), top_k=3)
        )
    except Exception:
        logger.exception("review.kb_retrieve failed")

    user = prompts.REVIEW_USER.format(
        title=title,
        content=content,
        forbidden_words=", ".join(forbidden_words) or "(none)",
        similar_history=join_chunks(similar_chunks),
    )
    # 主题摘要注入：REVIEW 做一致性检查时要知道这条稿子围绕什么主题
    brief_section = format_brief(state.get("brief") if isinstance(state.get("brief"), dict) else None)
    if brief_section:
        user = f"## 主题摘要（来自 chat 对话）\n{brief_section}\n\n" + user

    score_dup = 0.0
    score_human = 1.0
    forbidden_hits: list[str] = []
    passed = False
    reason = ""
    try:
        raw = await llm_complete(prompts.REVIEW_SYSTEM, user, call_type="review")
        parsed = parse_json_response(raw)
        forbidden_hits = [
            h for h in (parsed.get("forbidden_hits") or []) if h
        ]
        forbidden_hits += _hit_locally(title + "\n" + content, forbidden_words)
        # 去重
        seen: set[str] = set()
        forbidden_hits = [h for h in forbidden_hits if not (h in seen or seen.add(h))]

        score_dup = float(parsed.get("score_dup") or 0.0)
        score_human = float(parsed.get("score_human") or 0.0)
        passed = bool(parsed.get("passed"))
        reason = str(parsed.get("reason") or "")
    except Exception as exc:
        logger.exception("review.llm failed; falling back to local checks only")
        # LLM 不可用 → 退化为仅做违禁词 + 关键词覆盖判断
        forbidden_hits = _hit_locally(title + "\n" + content, forbidden_words)
        reason = f"review_llm_failed: {exc}"

    # 强制本地校验：违禁词命中立刻判失败
    if forbidden_hits:
        passed = False
        reason = (reason + "; forbidden_hit:" + ",".join(forbidden_hits)).strip("; ")

    review = {
        "passed": passed,
        "score_dup": score_dup,
        "score_human": score_human,
        "forbidden_hits": forbidden_hits,
        "reason": reason,
    }
    return {
        "review": review,
        "review_rules": [{"text": w, "doc_type": "rule"} for w in forbidden_hits],
        "last_error": None,
    }


def _hit_locally(text: str, forbidden: list[str]) -> list[str]:
    hits: list[str] = []
    lower = (text or "").lower()
    for w in forbidden:
        if w and w.lower() in lower:
            hits.append(w)
    return hits
