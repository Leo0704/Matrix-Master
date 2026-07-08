"""RESEARCH 节点：基于 goal + 知识库检索候选选题。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from .. import prompts
from .._services import get_services, llm_complete
from ..types import AgentState, State
from ._util import format_brief, join_chunks, parse_json_response

logger = logging.getLogger(__name__)


async def research_node(state: AgentState) -> dict[str, Any]:
    """检索 topic / history / rule，调 LLM 选 1-N 个候选。

    langgraph partial update:
        - ``research_chunks``: 检索到的原始 chunks（保留给后续节点可选参考）
        - ``candidates``: 候选选题列表（typed-dict shape）
        - ``selected_topic``: 默认选第一个，便于 DRAFT 节点直接消费
        - ``last_error``: 失败时填充
    """
    services = get_services()
    goal_text = state.get("goal_text") or ""
    brief = state.get("brief") if isinstance(state.get("brief"), dict) else None
    # 检索 query 优先用 brief["theme"]（具体主题）而非 goal_text（可能很口语化）
    brief_theme = brief.get("theme") if brief else None
    query_text = (brief_theme or goal_text).strip() or goal_text or "(no goal)"

    # 1. KB 检索
    topic_chunks: list = []
    history_chunks: list = []
    rules_chunks: list = []
    brand_chunks: list = []
    persona_chunks: list = []
    try:
        from ..protocols import RetrieveQuery

        topic_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=query_text, doc_types=("topic",), top_k=10)
        )
        history_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=query_text, doc_types=("history",), top_k=5)
        )
        rules_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=query_text, doc_types=("rule",), top_k=3)
        )
        brand_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=query_text, doc_types=("brand",), top_k=2)
        )
        persona_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=query_text, doc_types=("persona",), top_k=2)
        )
    except Exception as exc:  # KB 检索失败 → 留 candidates 空，转 ALERT
        logger.exception("research.kb_retrieve failed")
        return {
            State.ANALYZE.value: "RESEARCH",
            "candidates": [],
            "selected_topic": None,
            "research_chunks": [],
            "last_error": {"code": "KB_RETRIEVE_FAILED", "message": str(exc)},
        }

    # 2. LLM 选 1-N 个（k 默认 3）
    k = 3
    brief_section = format_brief(brief)
    user = prompts.RESEARCH_USER.format(
        goal=goal_text or "(no explicit goal)",
        brand=join_chunks(brand_chunks),
        persona=join_chunks(persona_chunks),
        topics=join_chunks(topic_chunks, limit=10),
        history=join_chunks(history_chunks),
        rules=join_chunks(rules_chunks),
    )
    if brief_section:
        user = f"## 主题摘要（来自 chat 对话）\n{brief_section}\n\n" + user
    try:
        raw = await llm_complete(
            prompts.RESEARCH_SYSTEM.format(k=k),
            user,
        )
    except Exception as exc:
        logger.exception("research.llm failed")
        return {
            "candidates": [],
            "selected_topic": None,
            "research_chunks": topic_chunks + history_chunks + rules_chunks,
            "last_error": {"code": "LLM_FAILED", "message": str(exc)},
        }

    parsed = parse_json_response(raw)
    selected_raw = parsed.get("selected") or []
    candidates: list[dict[str, Any]] = []
    for item in selected_raw[:k]:
        candidates.append(
            {
                "topic_id": uuid4(),  # 临时 id；后续 ANALYZE 可绑定到 topics 表
                "title": str(item.get("title", "")).strip(),
                "rationale": str(item.get("rationale", "")).strip(),
            }
        )
    # 兜底：LLM 没出合法 JSON 时，从 topic 检索结果里直接取前 k 个
    if not candidates and topic_chunks:
        for c in topic_chunks[:k]:
            candidates.append(
                {
                    "topic_id": getattr(c, "chunk_id", uuid4()),
                    "title": c.text.split("\n", 1)[0][:120],
                    "rationale": "fallback from KB topic retrieval",
                }
            )

    return {
        "candidates": candidates,
        "selected_topic": candidates[0] if candidates else None,
        "research_chunks": topic_chunks + history_chunks + rules_chunks,
        "last_error": None,
    }
