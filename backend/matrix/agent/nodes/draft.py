"""DRAFT 节点：基于 selected_topic + persona 调 LLM 生成文案。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any
from uuid import uuid4

from .. import prompts
from .._services import get_services, llm_complete
from ..protocols import RetrieveQuery
from ..types import AgentState
from ._util import format_brief, join_chunks, parse_json_response

logger = get_logger(__name__)


async def draft_node(state: AgentState) -> dict[str, Any]:
    """生成文案 + 配图占位 + tags。

    不持久化到 notes 表（dispatch 时才落 notes）。
    """
    services = get_services()
    topic = state.get("selected_topic") or {}
    topic_title = topic.get("title", "") if isinstance(topic, dict) else ""
    topic_rationale = topic.get("rationale", "") if isinstance(topic, dict) else ""
    # 1. 取 persona / 违禁词 / 品牌
    persona_chunks = []
    rule_chunks = []
    brand_chunks = []
    # 2. 取历史复盘经验卡（analyze 节点提炼写入），优先于 raw history
    strategy_card_chunks = []
    history_chunks = []
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
        # 自我进化闭环：先取提炼好的 strategy_card，再辅以 history 原文
        strategy_card_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=topic_title, doc_types=("strategy_card",), top_k=5)
        )
        history_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=topic_title, doc_types=("history",), top_k=3)
        )
    except Exception:
        logger.exception("draft.kb_retrieve failed")
        rule_chunks, persona_chunks, brand_chunks = [], [], []
        strategy_card_chunks, history_chunks = [], []

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
        strategy_cards_section=_format_strategy_cards(strategy_card_chunks),
        history_section=_format_history_chunks(history_chunks),
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


def _format_strategy_cards(chunks: list) -> str:
    """把 strategy_card chunks 拼成 prompt 可读的经验清单。

    chunk.text 是 analyze 节点写入的 JSON（见 ``_format_strategy_card``）。
    取 ``lessons`` 字段作为本次写稿的"教训清单"；解析失败时 fallback 到原文前 200 字。
    """
    import json

    if not chunks:
        return "(none)"
    lines: list[str] = []
    for c in chunks:
        text = getattr(c, "text", "") or ""
        lessons: list[str] = []
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                ls = payload.get("lessons") or []
                if isinstance(ls, list):
                    lessons = [str(x) for x in ls if x]
        except Exception:
            # fallback：原文前 200 字
            lessons = [text[:200].strip()] if text.strip() else []
        if not lessons:
            continue
        lines.append("- " + " | ".join(lessons))
    return "\n".join(lines) if lines else "(none)"


def _format_history_chunks(chunks: list) -> str:
    """把 history chunks 拼成简短摘要（标题 + 关键数据），不抢策略卡的版面。"""
    if not chunks:
        return "(none)"
    lines: list[str] = []
    for c in chunks:
        text = (getattr(c, "text", "") or "").strip()
        if not text:
            continue
        # history 的 content 第一行是 "# 标题"，取它作为可读摘要的前缀
        head = text.split("\n", 1)[0].lstrip("# ").strip()
        # 取 metrics 行（如 "views=0, likes=12, ..."），作为效果佐证
        snippet = text[:200]
        lines.append(f"- {head} | {snippet[:120]}")
    return "\n".join(lines) if lines else "(none)"
