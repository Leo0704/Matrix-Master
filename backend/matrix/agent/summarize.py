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
from dataclasses import dataclass
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
        # 关联 note：note 表没有 goal_id 字段，但 payload.brief → notes 没直接关系。
        # 简化：按 run.started_at 时间窗口前后 1h 内查 created 的 note（最多 1 条）。
        # 实际生产应该有 notes.goal_id 或 notes.source_run_id 字段；这里降级用时间窗。
        from datetime import timedelta

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
    )


_SUMMARIZE_SYSTEM = (
    "你是运营复盘助手。给一段 goal 下的所有 run 数据（每条 run 含标题、正文、tags、状态、"
    "views/likes/collects/comments），提炼两类经验，**严格返回 JSON**：\n"
    "{\n"
    '  "viral_patterns": ["爆款标题模板或封面风格 1", "爆款标题模板或封面风格 2", ...],\n'
    '  "failure_lessons": ["不该踩的坑 1（禁词/违规/低效）", "不该踩的坑 2", ...]\n'
    "}\n"
    "每条 1 句话，30 字内。看不到数据就别编。无数据时返回空数组。"
)


async def _ask_llm_for_learnings(snapshot: GoalSnapshot) -> dict[str, list[str]]:
    """调 LLM 提炼爆款/失败。失败时返回空 dict，不阻塞。"""
    if not snapshot.runs:
        return {"viral_patterns": [], "failure_lessons": []}
    user_payload = {
        "goal_theme": snapshot.theme,
        "goal_audience": snapshot.audience,
        "runs": snapshot.runs,
    }
    user = json.dumps(user_payload, ensure_ascii=False)
    try:
        raw = await llm_complete(_SUMMARIZE_SYSTEM, user)
    except Exception:
        logger.exception("summarize.llm_failed", goal_id=str(snapshot.goal_id))
        return {"viral_patterns": [], "failure_lessons": []}
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
        return {"viral_patterns": [], "failure_lessons": []}
    return {
        "viral_patterns": [str(x) for x in (data.get("viral_patterns") or [])][:10],
        "failure_lessons": [str(x) for x in (data.get("failure_lessons") or [])][:10],
    }


async def summarize_goal_to_kb(
    session: AsyncSession,
    embedder: EmbeddingService,
    goal_id: uuid.UUID,
) -> list[KbDocument]:
    """复盘一个 goal：拉数据 → 调 LLM → 写 2 篇 KB doc（strategy_card + rule）。

    Returns:
        写出的 KbDocument 列表（可能为空，如果 goal 不存在或 LLM 啥也没提炼到）。
    """
    snapshot = await _load_goal_snapshot(session, goal_id)
    if snapshot is None:
        return []

    learnings = await _ask_llm_for_learnings(snapshot)
    if not learnings["viral_patterns"] and not learnings["failure_lessons"]:
        return []

    store = KbStore(session, embedder)
    written: list[KbDocument] = []

    # 1) 爆款模板 → strategy_card
    if learnings["viral_patterns"]:
        body = (
            f"# 爆款模式（goal: {snapshot.theme}）\n\n"
            + "\n".join(f"- {p}" for p in learnings["viral_patterns"])
        )
        doc = await store.create_document(
            type="strategy_card",
            content=body,
            title=f"爆款模板 · {snapshot.theme[:40]}",
            ref_id=goal_id,
            metadata={
                "goal_id": str(goal_id),
                "source": "goal_summarize",
                "audience": snapshot.audience,
                "run_count": len(snapshot.runs),
            },
            is_published=False,  # 等人工 review
        )
        written.append(doc)

    # 2) 失败教训 → rule
    if learnings["failure_lessons"]:
        body = (
            f"# 失败教训（goal: {snapshot.theme}）\n\n"
            + "\n".join(f"- {p}" for p in learnings["failure_lessons"])
        )
        doc = await store.create_document(
            type="rule",
            content=body,
            title=f"避坑规则 · {snapshot.theme[:40]}",
            ref_id=goal_id,
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
        viral=len(learnings["viral_patterns"]),
        failures=len(learnings["failure_lessons"]),
    )
    return written


__all__ = ["GoalSnapshot", "summarize_goal_to_kb"]
