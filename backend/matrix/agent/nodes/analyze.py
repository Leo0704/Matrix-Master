"""ANALYZE 节点：复盘 + 写历史到 KB + 提炼经验卡。"""

from __future__ import annotations

import json

from matrix.monitoring.logging import get_logger
from typing import Any

from .. import prompts
from .._services import get_services, llm_complete
from ..protocols import RetrieveQuery
from ..types import AgentState
from ._util import format_brief, join_chunks, parse_json_response

logger = get_logger(__name__)


async def analyze_node(state: AgentState) -> dict[str, Any]:
    """调 LLM 出复盘文本 + strategy_updates；通过 ``kb_writer.upsert_document``
    把这次笔记作为一条 ``type=history`` 文档写入 KB，并把 strategy_updates
    提炼成一条 ``type=strategy_card`` 文档（draft 节点下次写稿时优先召回）。
    """
    services = get_services()
    draft = state.get("draft") or {}
    metrics = state.get("note_metrics") or {}

    persona_style = ""
    rules = ""
    try:
        persona_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=str(draft.get("title", "")), doc_types=("persona",), top_k=1)
        )
        rule_chunks = await services.kb_retriever.retrieve(
            RetrieveQuery(query=str(draft.get("title", "")), doc_types=("rule",), top_k=2)
        )
        persona_style = join_chunks(persona_chunks, limit=1)
        rules = join_chunks(rule_chunks, limit=2)
    except Exception:
        logger.exception("analyze.kb_retrieve failed")

    user = prompts.ANALYZE_USER.format(
        title=draft.get("title", ""),
        content=draft.get("content", ""),
        tags=draft.get("tags") or [],
        views=metrics.get("views", 0),
        likes=metrics.get("likes", 0),
        collects=metrics.get("collects", 0),
        comments=metrics.get("comments", 0),
        follows_gained=metrics.get("follows_gained", 0),
        persona_style=persona_style or "(no persona)",
        rules=rules or "(no rules)",
    )
    # 主题摘要注入：复盘时要能引用主题/人群来评判效果
    brief_section = format_brief(state.get("brief") if isinstance(state.get("brief"), dict) else None)
    if brief_section:
        user = f"## 主题摘要（来自 chat 对话）\n{brief_section}\n\n" + user

    try:
        raw = await llm_complete(prompts.ANALYZE_SYSTEM, user, call_type="analyze")
        parsed = parse_json_response(raw)
        review_text = str(parsed.get("review_text") or "").strip()
        strategy_updates = parsed.get("strategy_updates") or []
        if isinstance(strategy_updates, str):
            strategy_updates = [strategy_updates]
        strategy_updates = [str(x) for x in strategy_updates if x][:5]
    except Exception:
        logger.exception("analyze.llm failed")
        review_text = ""
        strategy_updates = []

    # 把本次发布写为一条 history 文档
    content = _format_history_content(
        title=draft.get("title", ""),
        body=draft.get("content", ""),
        tags=draft.get("tags") or [],
        metrics=metrics,
        review=review_text,
        strategy_updates=strategy_updates,
    )
    try:
        await services.kb_writer.upsert_document(
            doc_type="history",
            ref_id=None,
            title=str(draft.get("title", ""))[:256] or None,
            content=content,
            metadata={
                "metrics": metrics,
                "tags": draft.get("tags") or [],
                "review": review_text,
                "strategy_updates": strategy_updates,
            },
        )
    except Exception:
        logger.exception("analyze.kb_writer.upsert_document failed")
        # 写库失败不阻塞 state machine 回到 IDLE；上层会重试或告警

    # 提炼 strategy_card：把本次 strategy_updates 装成可被 draft 节点下次召回的
    # 结构化经验卡。一条 run 一张卡，content 是 JSON（lesson + 证据 + 适用主题）。
    if strategy_updates:
        try:
            card_content = _format_strategy_card(
                lessons=strategy_updates,
                metrics=metrics,
                tags=draft.get("tags") or [],
            )
            await services.kb_writer.upsert_document(
                doc_type="strategy_card",
                ref_id=None,
                title=strategy_updates[0][:256],
                content=card_content,
                metadata={
                    "lessons": strategy_updates,
                    "metrics": metrics,
                    "tags": draft.get("tags") or [],
                    "confidence": "low",  # 单 run 样本，置信度固定 low；累积后人工/统计再升档
                },
            )
        except Exception:
            logger.exception("analyze.kb_writer.upsert_document strategy_card failed")

    return {
        "last_error": None,
    }


def _format_history_content(
    *,
    title: str,
    body: str,
    tags: list[str],
    metrics: dict[str, int],
    review: str,
    strategy_updates: list[str],
) -> str:
    parts = [
        f"# {title}\n",
        body,
        "\n## tags",
        ", ".join(tags),
        "\n## metrics (24h)",
        ", ".join(f"{k}={metrics.get(k, 0)}" for k in ("views", "likes", "collects", "comments", "follows_gained")),
    ]
    if review:
        parts += ["\n## review", review]
    if strategy_updates:
        parts += ["\n## strategy_updates", "\n".join(f"- {x}" for x in strategy_updates)]
    return "\n".join(parts)


def _format_strategy_card(
    *,
    lessons: list[str],
    metrics: dict[str, int],
    tags: list[str],
) -> str:
    """把 strategy_updates 装成结构化经验卡（JSON 字符串，便于检索命中 + 渲染）。

    schema:
      {
        "lessons": [str, ...],
        "evidence_metrics": {"views": int, "likes": int, ...},
        "applies_to_tags": [str, ...],
      }
    """
    payload = {
        "lessons": lessons,
        "evidence_metrics": {k: int(metrics.get(k, 0)) for k in ("views", "likes", "collects", "comments", "follows_gained")},
        "applies_to_tags": tags,
    }
    return json.dumps(payload, ensure_ascii=False)
