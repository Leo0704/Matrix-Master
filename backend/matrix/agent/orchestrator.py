"""Goal-level orchestrator：1 个 goal 多轮运营。

Phase 状态机（5 阶段 + DONE）：

  PENDING ──► PREPARING ──► EXECUTING ──► MONITORING ──► SUMMARIZING ──► DECIDING
                                                                          │
                                              ┌─── 续跑 ──── current_round+=1, 回 PREPARING
                                              │
                                              └─── 收工 ──── 写 status=achieved, phase=DONE

每轮"运营"流程：
  PREPARING   拆任务：调 LLM 出 N 个 brief，每个 brief = 1 个 run（调 goals 创建 run）
  EXECUTING   等所有 run 跑完（status 都不是 running）
  MONITORING  拉这一轮所有 note 的 metrics，存到 goal_rounds.kpi_summary
  SUMMARIZING 调 summarize_goal_to_kb 写 KB；把 LLM 提炼存到 goals.learning_summary
  DECIDING    判断是否续跑：
              - 达成 KPI（KPI 字段，暂用 likes 阈值）→ DONE
              - current_round >= max_rounds → DONE
              - deadline 到了 → DONE
              - 否则回 PREPARING 开始下一轮

注：第 1 期 MVP 不动 LangGraph（task 级状态机），复用现有 goal → run 链路。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import (
    AgentRun,
    Alert,
    Goal,
    GoalRound,
    Note,
    NoteMetric,
)
from matrix.monitoring.logging import get_logger

from ._services import llm_complete

logger = get_logger(__name__)


# Phase 常量
PHASE_PENDING = "PENDING"
PHASE_PREPARING = "PREPARING"
PHASE_EXECUTING = "EXECUTING"
PHASE_MONITORING = "MONITORING"
PHASE_SUMMARIZING = "SUMMARIZING"
PHASE_DECIDING = "DECIDING"
PHASE_DONE = "DONE"

PHASE_ORDER = (
    PHASE_PENDING,
    PHASE_PREPARING,
    PHASE_EXECUTING,
    PHASE_MONITORING,
    PHASE_SUMMARIZING,
    PHASE_DECIDING,
    PHASE_DONE,
)

# 每轮目标 notes 数（每篇 1 个 run）；先固定 3，可后续参数化
NOTES_PER_ROUND = 3

# 续跑阈值：本轮累计 likes 达到此数即收工；否则跑完 max_rounds
DEFAULT_KPI_LIKES_TARGET = 500


@dataclass
class OrchestratorResult:
    """一次推进的结果（让 worker 知道下一步要不要再调）。"""

    goal_id: uuid.UUID
    phase_before: str
    phase_after: str
    round_number: int
    notes_created_this_round: int
    action: str  # 描述这次推进干了啥（"prepared 3 tasks"/"monitored: 3 notes"）


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _set_phase(session: AsyncSession, goal: Goal, new_phase: str) -> None:
    """更新 goal.phase + phase_updated_at + updated_at。"""
    goal.phase = new_phase
    goal.phase_updated_at = _utcnow()
    goal.updated_at = _utcnow()
    await session.flush()


# ---------------------------------------------------------------------------
# PREPARING：拆任务 → 写 N 条 AgentRun（不调 LangGraph，由 runner 拉起）
# ---------------------------------------------------------------------------


def _decompose_system_prompt(n: int) -> str:
    return (
        "你是内容运营的拆任务助手。给一个 goal 主题 + 受众 + 历史经验（可选），"
        f"拆出 {n} 个**不同角度**的子任务（每个对应一篇笔记）。\n"
        "**严格返回 JSON 数组**，每项结构：\n"
        '{"theme": "本篇笔记的具体角度", "audience": "目标人群（可与原 goal 略有不同）",\n'
        ' "product_category": "品类（可空）", "angle_reason": "为什么这个角度"}\n'
        "要求：\n"
        f"- {n} 个角度尽量**不重叠**（如不同使用场景/不同痛点/不同人群细分）\n"
        "- 参考历史经验里的爆款模式，避开失败教训\n"
        "- 看不到数据/没历史就靠 goal 主题合理拆"
    )


async def _decompose_goal(
    session: AsyncSession, goal: Goal, learnings_text: str
) -> list[dict]:
    """调 LLM 把 goal 拆成 N 个不同 brief。失败时降级：从 KB 抽角度；再降级：N 份原 brief。"""
    target = dict(goal.target or {})
    n = getattr(goal, "notes_per_round", NOTES_PER_ROUND) or NOTES_PER_ROUND
    user = json.dumps(
        {
            "goal_theme": target.get("theme", ""),
            "goal_audience": target.get("audience"),
            "goal_product_category": target.get("product_category"),
            "learnings": learnings_text,
        },
        ensure_ascii=False,
    )
    try:
        raw = await llm_complete(_decompose_system_prompt(n), user)
    except Exception:
        logger.exception("orchestrator.decompose.llm_failed", goal_id=str(goal.id))
        return []
    # 解析 JSON（兼容 ```json``` 包裹 + 单 dict 包成 list）
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("orchestrator.decompose.json_parse_fail", raw_preview=raw[:200])
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    # 规范化
    out: list[dict] = []
    for item in data[:n]:
        if not isinstance(item, dict):
            continue
        out.append({
            "theme": str(item.get("theme", ""))[:200],
            "audience": str(item.get("audience", target.get("audience", "")))[:100] or None,
            "product_category": str(item.get("product_category", target.get("product_category", "")))[:100] or None,
            "angle_reason": str(item.get("angle_reason", ""))[:200],
        })
    return out


async def _fallback_briefs_from_kb(
    session: AsyncSession, goal: Goal, n: int
) -> list[dict]:
    """LLM 拆任务失败时的智能降级：从 KB 已发布的 strategy_card 抽 n 个不同角度。

    比"N 份原 brief 复制"强一些：每条 brief 用 KB 里的真实爆款/避坑角度作主题。
    """
    from matrix.agent.learning_prompt import fetch_relevant_learnings

    theme = str((dict(goal.target or {})).get("theme", ""))
    audience = (dict(goal.target or {})).get("audience")
    learnings = await fetch_relevant_learnings(session, theme, audience, limit=n)
    # learnings 是 "## 历史经验...\n- [爆款] xxx：snippet\n..." 文本
    # 抽 bullet 行作为 angle
    angles: list[str] = []
    for line in learnings.splitlines():
        line = line.strip()
        if line.startswith("- "):
            angle = line[2:].strip()
            # 取冒号前作为简短 angle
            if "：" in angle:
                angle = angle.split("：", 1)[1].strip() if len(angle.split("：", 1)) > 1 else angle
            elif ":" in angle:
                angle = angle.split(":", 1)[1].strip() if len(angle.split(":", 1)) > 1 else angle
            # 截断到 80 字
            angles.append(angle[:80])
    # 兜底：抽不到就用原 brief
    target = dict(goal.target or {})
    if not angles:
        return [target] * n
    # n 个不同 angle，每个 angle + 原 brief 合并
    out: list[dict] = []
    for i in range(n):
        if i < len(angles):
            merged = dict(target)
            merged["theme"] = angles[i]
            merged["angle_reason"] = f"参考 KB 历史：{angles[i]}"
            out.append(merged)
        else:
            out.append(target)
    return out


async def _prepare_round(
    session: AsyncSession, goal: Goal, round_number: int
) -> int:
    """在 goal 上开 1 轮：LLM 拆 N 个不同 brief，每条 run 一个 brief。

    拆任务失败时降级：跑 N 次原 brief（不阻挡流程）。
    """
    target = dict(goal.target or {})

    # 拉历史经验（第 3 期 learning_prompt）
    learnings_text = ""
    try:
        from matrix.agent.learning_prompt import fetch_relevant_learnings
        learnings_text = await fetch_relevant_learnings(
            session,
            theme=str(target.get("theme", "")),
            audience=target.get("audience"),
        )
    except Exception:
        logger.exception("orchestrator.learnings_fetch_failed", goal_id=str(goal.id))

    # 调 LLM 拆任务
    n = getattr(goal, "notes_per_round", NOTES_PER_ROUND) or NOTES_PER_ROUND
    briefs = await _decompose_goal(session, goal, learnings_text)
    if not briefs:
        # 降级 1：从 KB 抽 n 个不同角度
        briefs = await _fallback_briefs_from_kb(session, goal, n)
    if not briefs:
        # 降级 2：n 份原 brief
        briefs = [target] * n

    created = 0
    for brief in briefs[:n]:
        # 合并原 target 字段（保留 goal_type 等）但用新拆的 theme/audience
        merged = dict(target)
        if brief.get("theme"):
            merged["theme"] = brief["theme"]
        if brief.get("audience"):
            merged["audience"] = brief["audience"]
        if brief.get("product_category"):
            merged["product_category"] = brief["product_category"]
        if brief.get("angle_reason"):
            merged["angle_reason"] = brief["angle_reason"]
        merged["round_number"] = round_number
        payload = {
            "brief": merged,
            "entry": "RESEARCH",
            "goal_text": (merged.get("theme") or target.get("theme", ""))[:200],
            "goal_type": goal.type,
            "round_number": round_number,
        }
        run = AgentRun(
            goal_id=goal.id,
            current_state="IDLE",
            payload=payload,
            status="running",
        )
        session.add(run)
        created += 1
    # 写 1 条 goal_rounds 记录
    round_row = GoalRound(
        goal_id=goal.id,
        round_number=round_number,
        started_at=_utcnow(),
    )
    session.add(round_row)
    await session.flush()
    return created


# ---------------------------------------------------------------------------
# EXECUTING：等所有 run 跑完
# ---------------------------------------------------------------------------


async def _check_runs_done(session: AsyncSession, goal_id: uuid.UUID) -> bool:
    """所有本轮 run 都不在 running → 可以进 MONITORING。"""
    stmt = select(func.count(AgentRun.id)).where(
        AgentRun.goal_id == goal_id,
        AgentRun.status == "running",
    )
    pending = (await session.execute(stmt)).scalar() or 0
    return pending == 0


# ---------------------------------------------------------------------------
# MONITORING：拉 KPI 写到 goal_rounds
# ---------------------------------------------------------------------------


async def _gather_round_kpi(
    session: AsyncSession, goal_id: uuid.UUID, round_number: int
) -> dict[str, Any]:
    """拉这一轮所有 note 的最新 metrics，汇总后写到 goal_rounds.kpi_summary。

    简化：直接查全部 agent_runs 的 goal_id，按时间窗关联最近 notes。
    实际生产应该用 notes.goal_id 字段（待加）。
    """
    # 拉所有 run
    runs_stmt = select(AgentRun).where(AgentRun.goal_id == goal_id)
    runs = (await session.execute(runs_stmt)).scalars().all()

    total_views = 0
    total_likes = 0
    total_collects = 0
    total_comments = 0
    notes_count = 0
    per_note: list[dict[str, Any]] = []

    for run in runs:
        # 关联 note：MVP 简化用时间窗
        from datetime import timedelta

        window = timedelta(hours=2)
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
            continue
        notes_count += 1
        # 最新 metrics
        metric_stmt = (
            select(NoteMetric)
            .where(NoteMetric.note_id == note.id)
            .order_by(NoteMetric.ts.desc())
            .limit(1)
        )
        metric = (await session.execute(metric_stmt)).scalars().first()
        if metric is not None:
            total_views += metric.views
            total_likes += metric.likes
            total_collects += metric.collects
            total_comments += metric.comments
            per_note.append({
                "note_id": str(note.id),
                "title": note.title,
                "views": metric.views,
                "likes": metric.likes,
                "collects": metric.collects,
                "comments": metric.comments,
            })

    return {
        "total_views": total_views,
        "total_likes": total_likes,
        "total_collects": total_collects,
        "total_comments": total_comments,
        "notes_count": notes_count,
        "per_note": per_note,
    }


async def _write_round_kpi(
    session: AsyncSession, goal_id: uuid.UUID, round_number: int, kpi: dict[str, Any]
) -> None:
    stmt = select(GoalRound).where(
        GoalRound.goal_id == goal_id, GoalRound.round_number == round_number
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        return
    row.kpi_summary = kpi
    row.notes_created = kpi.get("notes_count", 0)
    row.total_views = kpi.get("total_views", 0)
    row.total_likes = kpi.get("total_likes", 0)
    row.ended_at = _utcnow()
    await session.flush()


# ---------------------------------------------------------------------------
# SUMMARIZING：调 LLM 写复盘 + 写 KB
# ---------------------------------------------------------------------------


async def _summarize_round(
    session: AsyncSession, goal: Goal, round_number: int, kpi: dict[str, Any]
) -> str:
    """写本轮 learning_summary（轻量 LLM 提炼），同时调 KB 复盘模块。"""
    # 1) 调 KB 复盘模块（独立可运行，第 3 期已做）
    try:
        from matrix.agent.summarize import summarize_goal_to_kb
        from matrix.kb._singleton import get_embedder as _get_global_embedder
        from matrix.kb.embedding import EmbeddingService
        from matrix.llm.embeddings import EmbeddingClient
        from matrix.config import get_settings

        s = get_settings()
        embedder: EmbeddingService = await _get_global_embedder(
            EmbeddingClient,
            api_key=s.openai_api_key,
            base_url=s.embedding_base_url,
        )
        kb_docs = await summarize_goal_to_kb(session, embedder, goal.id)
    except Exception:
        logger.exception("orchestrator.summarize.kb_failed", goal_id=str(goal.id))
        kb_docs = []

    # 1.5) 写 alert 通知老板"有 N 篇 KB 待 review"
    if kb_docs:
        try:
            alert = Alert(
                code="KB_REVIEW_PENDING",
                severity="info",
                message=(
                    f"Goal 第 {round_number} 轮复盘完成，"
                    f"已写入 {len(kb_docs)} 篇 KB（默认未发布，AI 看不到）。"
                    f"去知识库 review 后才能让下一轮拆任务时 LLM 参考。"
                ),
                subject_id=str(goal.id),
                resolved=False,
            )
            session.add(alert)
            await session.flush()
            logger.info(
                "orchestrator.kb_alert_created",
                goal_id=str(goal.id),
                round=round_number,
                kb_count=len(kb_docs),
            )
        except Exception:
            logger.exception("orchestrator.kb_alert_failed", goal_id=str(goal.id))

    # 2) 写 learning_summary（短文本，从 KPI 直接生成，先不调 LLM）
    summary = (
        f"第 {round_number} 轮：{kpi.get('notes_count', 0)} 篇稿，"
        f"总 {kpi.get('total_views', 0)} 浏览 / {kpi.get('total_likes', 0)} 赞。"
    )
    goal.learning_summary = summary
    goal.updated_at = _utcnow()
    await session.flush()
    return summary


# ---------------------------------------------------------------------------
# DECIDING：续跑 / 收工
# ---------------------------------------------------------------------------


def _should_continue(
    goal: Goal, kpi: dict[str, Any]
) -> tuple[bool, str]:
    """判断是否续跑。返回 (should_continue, reason)。

    KPI 阈值用 goal.target_likes（创建 goal 时可指定，缺省 500）。
    """
    # 1) deadline 到了 → 收工
    if goal.deadline is not None and _utcnow() >= goal.deadline:
        return False, f"deadline reached: {goal.deadline.isoformat()}"
    # 2) KPI 达成 → 收工（用 goal.target_likes 替代硬编码）
    target_likes = getattr(goal, "target_likes", DEFAULT_KPI_LIKES_TARGET) or DEFAULT_KPI_LIKES_TARGET
    if kpi.get("total_likes", 0) >= target_likes:
        return False, f"KPI achieved: {kpi.get('total_likes')}/{target_likes} likes"
    # 3) 跑满 max_rounds → 收工
    if goal.current_round >= goal.max_rounds:
        return False, f"max_rounds reached: {goal.max_rounds}"
    # 4) 否则续跑
    return True, f"continue: round {goal.current_round + 1}/{goal.max_rounds}"


# ---------------------------------------------------------------------------
# 推进 1 步：把 goal 从当前 phase 推到下一个 phase
# ---------------------------------------------------------------------------


async def advance_goal(
    session: AsyncSession,
    goal: Goal,
    *,
    kpi_likes_target: int = DEFAULT_KPI_LIKES_TARGET,
) -> Optional[OrchestratorResult]:
    """推进 goal 到下一 phase（按 PENDING→PREPARING→...→DONE 顺序）。

    Returns:
        OrchestratorResult 表示这次推进干了啥；None 表示没动（已经 DONE）。
    """
    if goal.phase == PHASE_DONE:
        return None
    if goal.phase not in PHASE_ORDER:
        logger.warning("orchestrator.unknown_phase", phase=goal.phase)
        return None

    phase_before = goal.phase
    action = ""

    if goal.phase == PHASE_PENDING:
        # 新 goal，第一轮从 PREPARING 开始
        await _set_phase(session, goal, PHASE_PREPARING)
        await session.commit()  # 必须 commit，否则 worker 下次扫还是 PENDING
        return OrchestratorResult(
            goal_id=goal.id,
            phase_before=phase_before,
            phase_after=PHASE_PREPARING,
            round_number=goal.current_round,
            notes_created_this_round=0,
            action="pending→preparing",
        )

    if goal.phase == PHASE_PREPARING:
        # 拆任务：创建 NOTES_PER_ROUND 条 run + 1 条 goal_rounds
        created = await _prepare_round(session, goal, goal.current_round)
        await _set_phase(session, goal, PHASE_EXECUTING)
        action = f"prepared {created} runs"
        await session.commit()
        return OrchestratorResult(
            goal_id=goal.id,
            phase_before=phase_before,
            phase_after=PHASE_EXECUTING,
            round_number=goal.current_round,
            notes_created_this_round=created,
            action=action,
        )

    if goal.phase == PHASE_EXECUTING:
        # 等所有 run 跑完
        done = await _check_runs_done(session, goal.id)
        if not done:
            # 还不能进 MONITORING，留在 EXECUTING
            return OrchestratorResult(
                goal_id=goal.id,
                phase_before=phase_before,
                phase_after=phase_before,
                round_number=goal.current_round,
                notes_created_this_round=0,
                action="executing: still running",
            )
        await _set_phase(session, goal, PHASE_MONITORING)
        return OrchestratorResult(
            goal_id=goal.id,
            phase_before=phase_before,
            phase_after=PHASE_MONITORING,
            round_number=goal.current_round,
            notes_created_this_round=0,
            action="all runs done→monitoring",
        )

    if goal.phase == PHASE_MONITORING:
        kpi = await _gather_round_kpi(session, goal.id, goal.current_round)
        await _write_round_kpi(session, goal.id, goal.current_round, kpi)
        await _set_phase(session, goal, PHASE_SUMMARIZING)
        await session.commit()
        return OrchestratorResult(
            goal_id=goal.id,
            phase_before=phase_before,
            phase_after=PHASE_SUMMARIZING,
            round_number=goal.current_round,
            notes_created_this_round=kpi.get("notes_count", 0),
            action=f"monitored: {kpi.get('notes_count', 0)} notes",
        )

    if goal.phase == PHASE_SUMMARIZING:
        # 拿上一步写的 kpi
        kpi = await _gather_round_kpi(session, goal.id, goal.current_round)
        await _summarize_round(session, goal, goal.current_round, kpi)
        await _set_phase(session, goal, PHASE_DECIDING)
        await session.commit()
        return OrchestratorResult(
            goal_id=goal.id,
            phase_before=phase_before,
            phase_after=PHASE_DECIDING,
            round_number=goal.current_round,
            notes_created_this_round=0,
            action="summarized",
        )

    if goal.phase == PHASE_DECIDING:
        kpi = await _gather_round_kpi(session, goal.id, goal.current_round)
        cont, reason = _should_continue(goal, kpi)
        if cont:
            goal.current_round += 1
            goal.learning_summary = (
                (goal.learning_summary or "")
                + f"\n[{reason}] → 第 {goal.current_round} 轮"
            )
            await _set_phase(session, goal, PHASE_PREPARING)
            await session.commit()
            return OrchestratorResult(
                goal_id=goal.id,
                phase_before=phase_before,
                phase_after=PHASE_PREPARING,
                round_number=goal.current_round,
                notes_created_this_round=0,
                action=f"continue: {reason}",
            )
        else:
            # 收工
            await _set_phase(session, goal, PHASE_DONE)
            goal.status = "achieved"
            goal.updated_at = _utcnow()
            goal.learning_summary = (
                (goal.learning_summary or "") + f"\n[收工] {reason}"
            )
            await session.commit()
            return OrchestratorResult(
                goal_id=goal.id,
                phase_before=phase_before,
                phase_after=PHASE_DONE,
                round_number=goal.current_round,
                notes_created_this_round=0,
                action=f"done: {reason}",
            )

    return None


__all__ = [
    "OrchestratorResult",
    "PHASE_PENDING",
    "PHASE_PREPARING",
    "PHASE_EXECUTING",
    "PHASE_MONITORING",
    "PHASE_SUMMARIZING",
    "PHASE_DECIDING",
    "PHASE_DONE",
    "PHASE_ORDER",
    "NOTES_PER_ROUND",
    "DEFAULT_KPI_LIKES_TARGET",
    "advance_goal",
]
