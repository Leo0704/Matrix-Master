"""从知识库召回历史经验，拼成"历史经验"小节文本，给 LLM prompt 用。

第 3 期"让复盘真有价值"的"入"那一半：第 1 期 orchestrator 拆任务时，
把这段文本塞进 prompt，让 LLM 写稿时参考历史爆款 / 避坑规则。

不做完整向量检索（避免引入额外依赖），先用 SQL LIKE 过滤 + 按更新时间倒序。
够 MVP，后面接 RAG 检索再升级。

**Phase 4 #3 结构化提取**：strategy_card 现在是 JSON（StrategyCard dataclass），
fetch_relevant_learnings 检测到 JSON 走 ``StrategyCard.render_for_prompt`` 渲染成
"硬规则"；老 markdown 文本降级为"软示例"（保留兼容）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import KbDocument
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

# 召回的 KB 类型
_LEARNING_TYPES = ("strategy_card", "rule")

# prompt 里"历史经验"小节标题
_PROMPT_HEADER = "## 历史经验（来自过去 goal 的复盘）\n"
_PROMPT_EMPTY = "（暂无相关历史经验）"


def _keyword_match(doc: KbDocument, keywords: list[str]) -> bool:
    """简易匹配：title / content / metadata 任一含关键词（中文按子串）。"""
    if not keywords:
        return True
    haystacks = " ".join(
        filter(
            None,
            [
                doc.title or "",
                doc.content or "",
                # metadata 可能是 dict；扁平化拼成字符串
                " ".join(f"{k}={v}" for k, v in (doc.metadata_ or {}).items()),
            ],
        )
    ).lower()
    return any(kw.lower() in haystacks for kw in keywords)


def _extract_keywords(theme: str, audience: str | None) -> list[str]:
    """从 theme + audience 提关键词：简单按 2~4 字切分（中文不切分整句，按 2-gram）。"""
    keywords: list[str] = []
    if theme:
        # 简单按非中英文字符切，再保留 2~6 字片段
        tokens = [t for t in theme.replace("|", " ").replace(",", " ").split() if t]
        keywords.extend(tokens)
        # 2-gram 兜底（中文经常没空格）
        if len(theme) >= 4:
            for i in range(len(theme) - 1):
                if not theme[i].isascii() and not theme[i + 1].isascii():
                    keywords.append(theme[i : i + 2])
    if audience:
        keywords.append(audience)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for k in keywords:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:15]


def _format_doc(doc: KbDocument) -> str:
    """单条 KB doc → prompt 片段。strategy_card 优先按结构化"硬规则"渲染。"""
    if doc.type == "strategy_card":
        # Phase 4 #3：先尝试按 JSON 解析 → 渲染硬规则
        from matrix.agent.summarize import StrategyCard

        card = StrategyCard.parse(doc.content)
        if card is not None:
            # 主题从 title 提（"爆款模板 · 平价百搭女鞋带货" → "平价百搭女鞋带货"）
            theme = (doc.title or "").removeprefix("爆款模板 ·").strip()
            return card.render_for_prompt(theme=theme)
    # 老 markdown 文本 / rule：软示例
    label = "爆款" if doc.type == "strategy_card" else "避坑"
    title = doc.title or label
    snippet = (doc.content or "")[:200].replace("\n", " ")
    return f"- [{label}] {title}：{snippet}"


async def fetch_relevant_learnings(
    session: AsyncSession,
    theme: str,
    audience: str | None = None,
    *,
    limit: int = 5,
) -> str:
    """拉跟当前 theme/audience 相关的历史 KB doc，拼成 prompt 文本。

    Args:
        session: DB session
        theme: 当前 goal 主题
        audience: 受众（可选）
        limit: 最多取几条（viral + failure 各半）

    Returns:
        markdown 文本（"## 历史经验..." 开头）。无相关时返回"（暂无相关历史经验）"。
    """
    keywords = _extract_keywords(theme, audience)
    if not keywords:
        return _PROMPT_HEADER + _PROMPT_EMPTY

    # 先拉已发布的 strategy_card + rule，按 updated_at desc 限 50 条
    stmt = (
        select(KbDocument)
        .where(
            KbDocument.type.in_(_LEARNING_TYPES),
            KbDocument.is_published.is_(True),
            KbDocument.deleted_at.is_(None),
        )
        .order_by(KbDocument.updated_at.desc())
        .limit(50)
    )
    rows = (await session.execute(stmt)).scalars().all()

    matched = [d for d in rows if _keyword_match(d, keywords)][:limit]
    if not matched:
        logger.info(
            "learning.no_match", theme=theme[:30], keywords=keywords[:5]
        )
        return _PROMPT_HEADER + _PROMPT_EMPTY

    lines = [_PROMPT_HEADER]
    for doc in matched:
        lines.append(_format_doc(doc))
    logger.info(
        "learning.fetched", theme=theme[:30], count=len(matched)
    )
    return "\n".join(lines)


__all__ = ["fetch_relevant_learnings"]
