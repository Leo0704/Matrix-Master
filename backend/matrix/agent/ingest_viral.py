"""粘贴爆款原文 → LLM 拆解 → 写知识库。

用户把【别人】的一篇小红书爆款文案直接粘进来（不走链接抓取）。本模块调 LLM
拆解"为什么火"，并按 analyze_node 的现成格式落库：

- 一条 ``type=history``「爆款记录」→ 直接发布（只读参考，供 AI 写稿检索）
- 若拆出可复用套路，一张 ``type=strategy_card``「套路卡」→ 默认草稿（未发布），
  运营在页面点「发布」后 draft 节点才会召回。

复用 analyze_node 的 ``_format_history_content`` / ``_format_strategy_card``，保证
和中控自动复盘写出的格式逐字一致。写库直接走 ``KbStore.create_document``（与
summarize.py 一致），不走 kb_writer。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from matrix.agent import prompts
from matrix.agent._services import llm_complete
from matrix.agent.nodes._util import parse_json_response
from matrix.agent.nodes.analyze import _format_history_content, _format_strategy_card
from matrix.db.models import KbDocument
from matrix.kb.embedding import EmbeddingService
from matrix.kb.store import KbStore
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

_METRIC_KEYS = ("views", "likes", "collects", "comments", "follows_gained")


async def ingest_viral_text_to_kb(
    session: AsyncSession,
    embedder: EmbeddingService,
    *,
    raw_text: str,
    title: str | None = None,
    metrics: dict[str, int] | None = None,
) -> tuple[KbDocument, bool]:
    """拆解一篇粘贴的爆款文案，写入 KB。

    Args:
        session: DB session（调用方负责 commit）
        embedder: embedding 服务
        raw_text: 用户粘贴的爆款原文（必填）
        title: 可选，用户手填标题；为空时由 LLM 从正文提炼
        metrics: 可选，用户手填的数据（点赞/收藏/评论等）；缺失按 0

    Returns:
        (history_doc, strategy_card_created)
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("raw_text must be non-empty")

    user = prompts.VIRAL_INGEST_USER.format(title=title or "", raw_text=raw_text)
    try:
        response = await llm_complete(
            prompts.VIRAL_INGEST_SYSTEM, user, call_type="ingest_viral"
        )
        parsed = parse_json_response(response)
    except Exception:
        logger.exception("ingest_viral.llm_failed")
        parsed = {}

    parsed_title = str(parsed.get("title") or "").strip()
    body = str(parsed.get("body") or "").strip() or raw_text.strip()
    tags = [str(t) for t in (parsed.get("tags") or []) if t]
    review_text = str(parsed.get("review_text") or "").strip()
    strategy_updates = parsed.get("strategy_updates") or []
    if isinstance(strategy_updates, str):
        strategy_updates = [strategy_updates]
    strategy_updates = [str(x) for x in strategy_updates if x][:5]

    final_title = (title or parsed_title or "").strip()
    metrics_norm: dict[str, int] = {
        k: int((metrics or {}).get(k, 0) or 0) for k in _METRIC_KEYS
    }

    store = KbStore(session, embedder)

    # 1) 爆款记录 → history（直接发布：只读参考）
    content = _format_history_content(
        title=final_title,
        body=body,
        tags=tags,
        metrics=metrics_norm,
        review=review_text,
        strategy_updates=strategy_updates,
    )
    history_doc = await store.create_document(
        type="history",
        content=content,
        title=(final_title[:256] or None),
        metadata={
            "metrics": metrics_norm,
            "tags": tags,
            "review": review_text,
            "strategy_updates": strategy_updates,
            "source": "external_paste",
        },
        is_published=True,
    )

    # 2) 套路卡 → strategy_card（草稿，待人工发布）
    created_card = False
    if strategy_updates:
        card_content = _format_strategy_card(
            lessons=strategy_updates,
            metrics=metrics_norm,
            tags=tags,
        )
        await store.create_document(
            type="strategy_card",
            content=card_content,
            title=strategy_updates[0][:256],
            metadata={
                "lessons": strategy_updates,
                "metrics": metrics_norm,
                "tags": tags,
                "source": "external_paste",
                "confidence": "low",
            },
            is_published=False,
        )
        created_card = True

    logger.info(
        "ingest_viral.done",
        history_id=str(history_doc.id),
        strategy_card_created=created_card,
    )
    return history_doc, created_card


__all__ = ["ingest_viral_text_to_kb"]
