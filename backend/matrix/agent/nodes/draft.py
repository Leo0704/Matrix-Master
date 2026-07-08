"""DRAFT 节点：基于 selected_topic + persona 调 LLM 生成文案。"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from .. import prompts
from .._services import get_services, llm_complete
from ..protocols import RetrieveQuery
from ..types import AgentState
from ._util import format_brief, join_chunks, parse_json_response

logger = logging.getLogger(__name__)


async def draft_node(state: AgentState) -> dict[str, Any]:
    """生成文案 + 配图占位 + tags。

    不持久化到 notes 表（dispatch 时才落 notes）。
    """
    services = get_services()
    topic = state.get("selected_topic") or {}
    topic_title = topic.get("title", "") if isinstance(topic, dict) else ""
    topic_rationale = topic.get("rationale", "") if isinstance(topic, dict) else ""
    # 商品库检索 query：优先用 brief["theme"] 收窄到具体商品类目
    brief = state.get("brief") if isinstance(state.get("brief"), dict) else None
    product_query = (
        (brief.get("theme") if brief else None)
        or (brief.get("product_category") if brief else None)
        or topic_title
    ).strip() or topic_title or "(no product context)"

    # 1. 取 persona / 违禁词 / 品牌 / 商品事实
    persona_chunks = []
    rule_chunks = []
    brand_chunks = []
    product_chunks = []
    try:
        persona_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=topic_title, doc_types=("persona",), top_k=2)
        )
        rule_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=topic_title, doc_types=("rule",), top_k=3)
        )
        brand_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=topic_title, doc_types=("brand",), top_k=1)
        )
        # 商品事实库：按 brief 主题/类目检索
        product_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=product_query, doc_types=("product",), top_k=3)
        )
    except Exception:
        logger.exception("draft.kb_retrieve failed")
        rule_chunks, persona_chunks, brand_chunks, product_chunks = [], [], [], []

    persona_name = ""
    persona_tone = ""
    persona_style = ""
    forbidden_words: list[str] = []
    for c in persona_chunks:
        text = c.text or ""
        persona_name = persona_name or _first_line(text)
        persona_tone = persona_tone or _field(text, "tone")
        persona_style = persona_style or _field(text, "style")

    for c in rule_chunks:
        text = c.text or ""
        # 规则 chunk 中包含 [forbidden] 标记的逐字加入
        if "[forbidden]" in text.lower():
            word = text.split(":", 1)[-1].strip()
            if word.lower().startswith("[forbidden]"):
                word = word[len("[forbidden]"):].strip()
            if word:
                forbidden_words.append(word)

    user = prompts.DRAFT_USER.format(
        topic_title=topic_title,
        topic_rationale=topic_rationale,
        persona_name=persona_name or "default",
        persona_style=persona_style or "(no style guide)",
        persona_tone=persona_tone or "(neutral)",
        forbidden_words=", ".join(forbidden_words) or "(none)",
        brand=join_chunks(brand_chunks),
        product_facts=join_chunks(product_chunks) if product_chunks else "(no product facts)",
    )
    # 主题摘要（brief）作为最高优先级上下文放 prompt 顶部
    brief_section = format_brief(state.get("brief") if isinstance(state.get("brief"), dict) else None)
    if brief_section:
        user = f"## 主题摘要（来自 chat 对话）\n{brief_section}\n\n" + user

    try:
        raw = await llm_complete(
            prompts.DRAFT_SYSTEM.format(persona_name=persona_name or "default"),
            user,
        )
    except Exception as exc:
        logger.exception("draft.llm failed")
        return {
            "draft": None,
            "last_error": {"code": "DRAFT_LLM_FAILED", "message": str(exc)},
        }

    parsed = parse_json_response(raw)
    title = str(parsed.get("title", "")).strip() or topic_title
    content = str(parsed.get("content", "")).strip()
    tags = parsed.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    tags = [str(t) for t in tags if t][:8]

    draft = {
        "note_id": uuid4(),
        "title": title,
        "content": content,
        "images": [],  # 配图占位；上传图片交给 publish 之前的 IMAGE_GEN 流程
        "tags": tags,
    }
    return {
        "draft": draft,
        "review_rules": rule_chunks,
        "last_error": None,
    }


def _first_line(text: str) -> str:
    return (text or "").split("\n", 1)[0].strip()


def _field(text: str, key: str) -> str:
    """从 chunk 文本中提取 ``key: value`` 行。"""
    needle = f"{key}:"
    for line in (text or "").splitlines():
        if line.lower().startswith(needle):
            return line.split(":", 1)[1].strip()
    return ""
