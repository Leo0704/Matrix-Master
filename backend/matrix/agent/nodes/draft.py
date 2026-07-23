"""DRAFT 节点：基于 selected_topic + persona 调 LLM 生成文案。"""

from __future__ import annotations

import re

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

    # 优先用账号绑定的 Persona（如果 preassigned_slot 里有 account_id）
    bound_persona = await _load_bound_persona(state)
    if bound_persona is not None:
        persona_name = bound_persona["name"]
        persona_tone = bound_persona["tone"]
        persona_style = bound_persona["style_guide"]
        forbidden_words.extend(bound_persona["forbidden_words"])
    else:
        # 没绑定时从知识库检索 persona 文档
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
    # 历史经验（orchestrator 拆任务时从 KB 拉取），放在 brief 之后、正文 prompt 之前
    learnings_text = state.get("learnings_text") or ""
    if learnings_text.strip():
        user = learnings_text + "\n\n" + user

    try:
        raw = await llm_complete(
            prompts.DRAFT_SYSTEM.format(persona_name=persona_name or "default"),
            user,
            call_type="draft",
            # Phase 2a B：把 goal_id 透传给 cost_guard，按 goal 维度计 token
            goal_id=str(state.get("goal_id") or "") or None,
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
    # v0.7 Phase 5：草稿阶段就落库 notes 表（status='draft'，account_id 待 DISPATCH 时绑）。
    # 这样即使后续 SCHEDULE/DISPATCH/PUBLISH 失败（无设备、无账号），老板在「草稿」页也能看到内容。
    note_writer = getattr(services, "note_writer", None)
    if note_writer is not None:
        try:
            await note_writer(
                {
                    "id": draft["note_id"],
                    "account_id": None,
                    # v0.7+ 第 2 期：透传 goal_id/run_id，让 _gather_round_kpi 走直查
                    "goal_id": state.get("goal_id"),
                    "run_id": state.get("run_id"),
                    # v0.7+ 业务归属（修漏写：notes.business_id 是 NOT NULL）
                    "business_id": state.get("business_id"),
                    "title": draft["title"],
                    "content": draft["content"],
                    "images": draft["images"],
                    "tags": draft["tags"],
                    "status": "draft",
                }
            )
        except Exception:
            logger.exception("draft.note_writer failed")

    return {
        "draft": draft,
        "review_rules": rule_chunks,
        "last_error": None,
    }


async def _load_bound_persona(state: AgentState) -> dict[str, Any] | None:
    """从 preassigned_slot 里拿 account_id，查账号绑定的知识库 persona 文档。

    Returns:
        None 表示没绑定或查不到；否则返回 {name, tone, style_guide, forbidden_words}
    """
    preassigned = state.get("preassigned_slot")
    if not isinstance(preassigned, dict):
        return None
    account_id_str = preassigned.get("account_id")
    if not account_id_str:
        return None

    try:
        from uuid import UUID
        from sqlalchemy import select
        from matrix.db.models import Account, KbDocument

        account_id = UUID(str(account_id_str))
        # 通过 services 的 session factory 查库
        services = get_services()
        session_factory = getattr(services, "session_factory", None)
        if session_factory is None:
            return None

        async with session_factory() as session:
            account = (
                await session.execute(select(Account).where(Account.id == account_id))
            ).scalar_one_or_none()
            if account is None or account.persona_id is None:
                return None
            doc = (
                await session.execute(
                    select(KbDocument).where(
                        KbDocument.id == account.persona_id,
                        KbDocument.type == "persona",
                    )
                )
            ).scalar_one_or_none()
            if doc is None:
                return None
            return {
                "name": doc.title or "",
                "tone": _field(doc.content or "", "tone"),
                "style_guide": _field(doc.content or "", "style"),
                "forbidden_words": _extract_forbidden(doc.content or ""),
            }
    except Exception:
        logger.exception("draft.load_bound_persona failed")
        return None


def _extract_forbidden(content: str) -> list[str]:
    """从知识库 persona 文档 content 里提取 [forbidden] 标记的词。"""
    words = []
    for line in content.splitlines():
        m = re.match(r"^\[(禁|forbidden)\]\s*(.+)$", line, re.IGNORECASE)
        if m:
            words.append(m.group(1).strip())
    return words


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
