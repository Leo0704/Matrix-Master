"""复盘 → 知识库：把 goal 的所有 run 数据喂给 LLM，提炼爆款/失败，写 KB。

第 3 期"让复盘真有价值"的"出"那一半：
- 输出一：viral_patterns → strategy_card（爆款标题/封面/发布时间段模板）
- 输出二：failure_lessons → rule（不该踩的坑，禁词/违规/低效模式）

KB doc 默认 is_published=False，等运营人工 review 后再发布。
发布后，下一轮 goal 拆任务时由 learning_prompt.fetch_relevant_learnings 召回。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import AgentRun, Goal, KbDocument, Note, NoteMetric
from matrix.kb.embedding import EmbeddingService
from matrix.kb.store import KbStore
from matrix.monitoring.logging import get_logger

from ._services import llm_complete

logger = get_logger(__name__)


@dataclass
class GoalSnapshot:
    """单个 goal 的复盘输入：goal 自身 + 它所有 run + 每个 run 对应 note + 最新 metrics。"""

    goal_id: uuid.UUID
    theme: str
    audience: str | None
    runs: list[dict[str, Any]]  # [{run_id, note_id, title, content, tags, status, views, likes, collects, comments}]
    business_id: uuid.UUID | None = None  # v0.7+ 业务归属（KB 写入落列用）


# ---------------------------------------------------------------------------
# StrategyCard：强类型爆款模板（Phase 4 #3 结构化提取）
# ---------------------------------------------------------------------------


@dataclass
class StrategyCard:
    """从单次 goal 复盘提炼出的结构化爆款模板。

    5 个字段都是 list[str]：
    - title_patterns：标题模板关键词或子串（"数字+痛点" → 标题里要有数字）
    - hook_phrases：开头钩子短语 / 模板（"救命" / "后悔没早买" / "30天实测"）
    - structure：内容结构顺序（["开头钩子","痛点场景","解决产品","价格锚","CTA"]）
    - tone_keywords：调性关键词（["平价","真实","测评","学生党"]）
    - forbidden_patterns：禁用模式（"绝对化用词" / "未验证数据" / "竞品直名"）

    之前 ``strategy_card.content`` 是 markdown 列表文本，DRAFT 节点只能"软引用"——
    LLM 拿到就当没看到。现在改成强类型字段，fetch_relevant_learnings 渲染成
    "硬规则"塞进 DRAFT prompt，LLM 必须遵守。
    """

    title_patterns: list[str] = field(default_factory=list)
    hook_phrases: list[str] = field(default_factory=list)
    structure: list[str] = field(default_factory=list)
    tone_keywords: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any(
            [self.title_patterns, self.hook_phrases, self.structure,
             self.tone_keywords, self.forbidden_patterns]
        )

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "title_patterns": list(self.title_patterns),
            "hook_phrases": list(self.hook_phrases),
            "structure": list(self.structure),
            "tone_keywords": list(self.tone_keywords),
            "forbidden_patterns": list(self.forbidden_patterns),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategyCard":
        def _list(key: str) -> list[str]:
            v = d.get(key) or []
            if not isinstance(v, list):
                return []
            out: list[str] = []
            for x in v:
                # 只收 str/int；dict/list 跳过（结构化卡片不要嵌套）
                if isinstance(x, (str, int, float)):
                    s = str(x).strip()
                    if s:
                        out.append(s)
            return out[:10]

        return cls(
            title_patterns=_list("title_patterns"),
            hook_phrases=_list("hook_phrases"),
            structure=_list("structure"),
            tone_keywords=_list("tone_keywords"),
            forbidden_patterns=_list("forbidden_patterns"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def parse(cls, raw: str | None) -> "StrategyCard | None":
        """从 KbDocument.content 反解 StrategyCard。

        旧版 strategy_card 是 markdown 文本（"# 爆款模式..."），解析失败返 None。
        新版是 JSON，解析失败也返 None（不入下游 prompt）。
        """
        if not raw or not raw.strip():
            return None
        text = raw.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        card = cls.from_dict(data)
        return None if card.is_empty() else card

    def render_for_prompt(self, theme: str = "") -> str:
        """把 StrategyCard 渲染成"硬规则"段，插到 DRAFT prompt 里。

        每条都是强指令（"标题必须..."/"开头必须是..."/"内容按此顺序..."），
        不是软示例。
        """
        lines: list[str] = []
        if theme:
            lines.append(f"# 复盘提炼规则（来自过往 goal：{theme}）")
        else:
            lines.append("# 复盘提炼规则（来自过往 goal 的复盘）")
        if self.title_patterns:
            lines.append(
                "- 【标题硬规则】标题里必须出现以下至少 1 个关键词/模式：\n  "
                + "、".join(self.title_patterns)
            )
        if self.hook_phrases:
            lines.append(
                "- 【开头硬规则】正文开头前 30 字必须是以下钩子之一（或同类改写）：\n  "
                + "、".join(self.hook_phrases)
            )
        if self.structure:
            lines.append(
                "- 【结构硬规则】正文必须按以下顺序组织段落：\n  "
                + " → ".join(self.structure)
            )
        if self.tone_keywords:
            lines.append(
                "- 【调性硬规则】用词风格贴这些关键词：\n  "
                + "、".join(self.tone_keywords)
            )
        if self.forbidden_patterns:
            lines.append(
                "- 【禁用硬规则】以下内容绝对不能写：\n  "
                + "、".join(self.forbidden_patterns)
            )
        return "\n".join(lines)


async def _load_goal_snapshot(session: AsyncSession, goal_id: uuid.UUID) -> GoalSnapshot | None:
    """从 DB 拉一个 goal 的所有 run + 关联 note + 最新 metrics。"""
    goal = await session.get(Goal, goal_id)
    if goal is None or goal.deleted_at is not None:
        return None
    target = dict(goal.target or {})
    theme = str(target.get("theme", ""))
    audience = target.get("audience")

    # 拉所有 run
    runs_stmt = select(AgentRun).where(AgentRun.goal_id == goal_id)
    runs = (await session.execute(runs_stmt)).scalars().all()

    items: list[dict[str, Any]] = []
    for run in runs:
        item: dict[str, Any] = {
            "run_id": str(run.id),
            "current_state": run.current_state,
            "status": run.status,
        }
        # 关联 note：v0.7+ 第 2 期 notes 已加 goal_id/run_id，优先直查；
        # 找不到再回退 1h 时间窗（留给老数据，附 WARNING）。
        note = None
        if run.id is not None:
            note = (
                await session.execute(
                    select(Note).where(Note.run_id == run.id)
                )
            ).scalars().first()

        if note is None:
            from datetime import timedelta

            logger.warning(
                "summarize.note_resolve_fallback_window",
                run_id=str(run.id),
                reason="no notes.run_id match",
            )
            window = timedelta(hours=1)
            note_stmt = (
                select(Note)
                .where(
                    Note.created_at >= run.started_at - window,
                    Note.created_at <= (run.ended_at or run.started_at) + window,
                )
                .order_by(Note.created_at.desc())
                .limit(1)
            )
            note = (await session.execute(note_stmt)).scalars().first()
        if note is None:
            items.append(item)
            continue
        item.update({
            "note_id": str(note.id),
            "title": note.title,
            "content": (note.content or "")[:500],  # 截断避免 prompt 爆
            "tags": list(note.tags or []),
            "status": note.status,
        })
        # 最新 metrics
        metric_stmt = (
            select(NoteMetric)
            .where(NoteMetric.note_id == note.id)
            .order_by(NoteMetric.ts.desc())
            .limit(1)
        )
        metric = (await session.execute(metric_stmt)).scalars().first()
        if metric is not None:
            item.update({
                "views": metric.views,
                "likes": metric.likes,
                "collects": metric.collects,
                "comments": metric.comments,
            })
        items.append(item)

    return GoalSnapshot(
        goal_id=goal_id,
        theme=theme,
        audience=audience,
        runs=items,
        business_id=goal.business_id,  # v0.7+ 业务归属（修漏写）
    )


_SUMMARIZE_SYSTEM = (
    "你是运营复盘助手。给一段 goal 下的所有 run 数据（每条 run 含标题、正文、tags、状态、"
    "views/likes/collects/comments），提炼两类经验，**严格返回 JSON**：\n"
    "{\n"
    '  "viral_patterns": ["爆款标题模板或封面风格 1", ...],\n'
    '  "failure_lessons": ["不该踩的坑 1", ...]\n'
    "}\n"
    "**Phase 4 #3 结构化提取**：viral_patterns 拆成 5 个强类型字段，"
    "让 DRAFT 节点当硬规则用：\n"
    "{\n"
    '  "structured_viral": {\n'
    '    "title_patterns": ["数字+痛点", "季节+人群", ...],\n'
    '    "hook_phrases": ["救命", "后悔没早买", "30天实测", ...],\n'
    '    "structure": ["开头钩子", "痛点场景", "解决产品", "价格锚", "CTA"],\n'
    '    "tone_keywords": ["平价", "真实", "测评", "学生党", ...],\n'
    '    "forbidden_patterns": ["绝对化用词", "未验证数据", "竞品直名", ...]\n'
    "  },\n"
    '  "failure_lessons": ["不该踩的坑 1", ...]\n'
    "}\n"
    "每条 1 句话，30 字内。看不到数据就别编。无数据时返回空数组。"
)


async def _ask_llm_for_learnings(
    snapshot: GoalSnapshot,
) -> tuple[StrategyCard, list[str]]:
    """调 LLM 提炼爆款（结构化 StrategyCard）+ 失败（list[str]）。

    失败时返空 StrategyCard + 空 list，不阻塞。
    """
    if not snapshot.runs:
        return StrategyCard(), []
    user_payload = {
        "goal_theme": snapshot.theme,
        "goal_audience": snapshot.audience,
        "runs": snapshot.runs,
    }
    user = json.dumps(user_payload, ensure_ascii=False)
    try:
        raw = await llm_complete(_SUMMARIZE_SYSTEM, user, call_type="summarize")
    except Exception:
        logger.exception("summarize.llm_failed", goal_id=str(snapshot.goal_id))
        return StrategyCard(), []
    # 解析 JSON（容错：去掉 ```json``` 包裹）
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("summarize.json_parse_fail", raw_preview=raw[:200])
        return StrategyCard(), []
    # structured_viral 缺失时回退老 viral_patterns（兼容老 prompt 返回）
    if isinstance(data.get("structured_viral"), dict):
        card = StrategyCard.from_dict(data["structured_viral"])
    else:
        # 老 prompt 把所有爆款经验塞到 viral_patterns 列表里 → 全部归到 hook_phrases
        legacy = [
            str(x) for x in (data.get("viral_patterns") or []) if x
        ][:10]
        card = StrategyCard(hook_phrases=legacy) if legacy else StrategyCard()
    failures = [
        str(x) for x in (data.get("failure_lessons") or []) if x
    ][:10]
    return card, failures


async def summarize_goal_to_kb(
    session: AsyncSession,
    embedder: EmbeddingService,
    goal_id: uuid.UUID,
    *,
    auto_publish: bool = False,
) -> list[KbDocument]:
    """复盘一个 goal：拉数据 → 调 LLM → 写 2 篇 KB doc（strategy_card + rule）。

    Phase 4 #3：``auto_publish=True`` 时自动把 ``strategy_card`` 标记为已发布。
    理由：爆款模板是 LLM 从本 goal 数据里提炼的（有人审过整个过程），
    不再卡人工 review 才能让下一 goal 的 DRAFT 立即读到——学习闭环才闭合。
    ``rule``（避坑）保留 ``is_published=False``：避坑规则可能误伤，宁可慢。

    Returns:
        写出的 KbDocument 列表（可能为空，如果 goal 不存在或 LLM 啥也没提炼到）。
    """
    snapshot = await _load_goal_snapshot(session, goal_id)
    if snapshot is None:
        return []

    card, failures = await _ask_llm_for_learnings(snapshot)
    if card.is_empty() and not failures:
        return []

    store = KbStore(session, embedder)
    written: list[KbDocument] = []

    # 1) 爆款模板 → strategy_card（Phase 4 #3：存 JSON，不再是 markdown 列表）
    if not card.is_empty():
        doc = await store.create_document(
            type="strategy_card",
            content=card.to_json(),  # 强类型 JSON
            title=f"爆款模板 · {snapshot.theme[:40]}",
            ref_id=goal_id,
            business_id=snapshot.business_id,  # v0.7+ 业务归属（修漏写）
            metadata={
                "goal_id": str(goal_id),
                "source": "goal_summarize",
                "audience": snapshot.audience,
                "run_count": len(snapshot.runs),
                # 结构化字段也存到 metadata，方便 SQL 检索 / 不解析 JSON
                "structured_viral": card.to_dict(),
            },
            # Phase 4 #3：自动发布爆款模板（rule 仍走 review）
            is_published=auto_publish,
        )
        written.append(doc)

    # 2) 失败教训 → rule（始终不自动发布 —— 避坑规则副作用大）
    if failures:
        body = (
            f"# 失败教训（goal: {snapshot.theme}）\n\n"
            + "\n".join(f"- {p}" for p in failures)
        )
        doc = await store.create_document(
            type="rule",
            content=body,
            title=f"避坑规则 · {snapshot.theme[:40]}",
            ref_id=goal_id,
            business_id=snapshot.business_id,  # v0.7+ 业务归属（修漏写）
            metadata={
                "goal_id": str(goal_id),
                "source": "goal_summarize",
                "audience": snapshot.audience,
            },
            is_published=False,
        )
        written.append(doc)

    logger.info(
        "summarize.goal.done",
        goal_id=str(goal_id),
        viral=sum(
            len(getattr(card, f)) for f in (
                "title_patterns", "hook_phrases", "structure",
                "tone_keywords", "forbidden_patterns",
            )
        ),
        failures=len(failures),
        auto_publish=auto_publish,
    )
    return written


__all__ = ["GoalSnapshot", "StrategyCard", "summarize_goal_to_kb"]
