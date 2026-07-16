"""Goal-level orchestrator：1 个 goal 多轮运营。

Phase 状态机（5 阶段 + DONE）：

  PENDING ──► PREPARING ──► EXECUTING ──► MONITORING ──► SUMMARIZING ──► DECIDING
                                                                          │
                                              ┌─── 续跑 ──── current_round+=1, 回 PREPARING
                                              │
                                              └─── 收工 ──── 写 status=achieved, phase=DONE

每轮"运营"流程（v0.7+ round-level fan-out）：
  PREPARING   拉所有 active device；每台 = 1 个 run（绑定 device+account+scheduled_at+style_hint）
              主题与 goal 一致；风格按 ``STYLE_ROTATION`` 轮换；时间 15 分钟错开
  EXECUTING   等所有 run 跑完（status 都不是 running）
  MONITORING  拉这一轮所有 note 的 metrics，存到 goal_rounds.kpi_summary
  SUMMARIZING 调 summarize_goal_to_kb 写 KB；把 LLM 提炼存到 goals.learning_summary
  DECIDING    判断是否续跑：
              - 达成 KPI（KPI 字段，暂用 likes 阈值）→ DONE
              - current_round >= max_rounds → DONE
              - deadline 到了 → DONE
              - 否则回 PREPARING 开始下一轮

降级路径：当 ``round_allocator`` 未注入或 active device 数 = 0 时，跑 N 份占位 brief
（每份 run 的 payload 不带 preassigned_slot，回退到旧 ``choose_slot`` 随机路径）。

注：第 1 期 MVP 不动 LangGraph（task 级状态机），复用现有 goal → run 链路。
"""

from __future__ import annotations

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
from matrix.scheduler.round_slot_allocator import TimeOutOfWindowError

from ._services import get_services

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

# 兼容旧测试 / 表单字段：goal.notes_per_round 缺省值
NOTES_PER_ROUND = 3
# 软上限：单轮最多扇出多少设备（防止 LLM 成本爆炸；DB 字段不强制）
DEFAULT_MAX_ROUND_FANOUT = 20
# 时间错开：每台设备间隔分钟数
DEFAULT_STAGGER_MINUTES = 15

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
# PREPARING：拉 active device → 调 round_allocator → 写 N 条 AgentRun
# ---------------------------------------------------------------------------


def _count_target_for_round(goal: Goal) -> int:
    """降级路径用：从 goal.notes_per_round 取数，缺省 NOTES_PER_ROUND，capped。"""
    n = getattr(goal, "notes_per_round", NOTES_PER_ROUND) or NOTES_PER_ROUND
    return min(max(int(n), 0), DEFAULT_MAX_ROUND_FANOUT)


async def _allocate_round_slots(
    session: AsyncSession,
    goal: Goal,
) -> tuple[list, int]:
    """调 round_allocator.allocate 拿 N 个 (device, account, time, style_hint)。

    返回 ``(slots, n_requested)``：
    - slots: 可能为 ``[]``（无候选 / 失败 / 超出活跃窗 / 未注入 services）
    - n_requested: 传给 allocate 的 n（= min(active_devices, cap)）

    失败时静默降级，由 caller 决定走占位 brief 路径。
    """
    try:
        services = get_services()
    except RuntimeError:
        # 没注入 services（早期/测试场景）→ 走降级
        return [], 0
    if services.round_allocator is None:
        return [], 0
    try:
        active = await services.round_allocator.count_active_devices()
    except Exception:
        logger.exception(
            "orchestrator.count_active_devices failed", goal_id=str(goal.id)
        )
        return [], 0
    # 第 1 期：N 计算统一为 min(_count_target_for_round(goal), active, MAX)
    # _count_target_for_round 取 notes_per_round 上限；active 受设备数约束；MAX 全局上限
    n_target = _count_target_for_round(goal)
    n = min(n_target, active, DEFAULT_MAX_ROUND_FANOUT)
    if n <= 0:
        return [], 0
    try:
        persona_cfg = services.system_metadata.get("persona_config")
        slots = await services.round_allocator.allocate(
            brief=dict(goal.target or {}),
            n=n,
            stagger_minutes=DEFAULT_STAGGER_MINUTES,
            persona_config=persona_cfg,
        )
        return slots, n
    except TimeOutOfWindowError as exc:
        logger.warning(
            "orchestrator.round_out_of_window",
            goal_id=str(goal.id),
            n=n,
            error=str(exc),
        )
        return [], n
    except Exception:
        logger.exception(
            "orchestrator.allocate failed", goal_id=str(goal.id)
        )
        return [], n


def _build_run_payload(
    goal: Goal,
    target: dict,
    round_number: int,
    slot,
    *,
    learnings_text: str = "",
) -> dict[str, Any]:
    """构造 AgentRun.payload；slot 为 None 时不带 preassigned_slot（旧随机路径）。"""
    brief = dict(target)
    if slot is not None:
        brief["style_hint"] = slot.style_hint
    payload: dict[str, Any] = {
        "brief": brief,
        "entry": "RESEARCH",
        "goal_text": (brief.get("theme") or target.get("theme", ""))[:200],
        "goal_type": goal.type,
        "round_number": round_number,
    }
    if learnings_text:
        payload["learnings_text"] = learnings_text
    if slot is not None:
        payload["preassigned_slot"] = {
            "device_id": str(slot.device_id),
            "account_id": str(slot.account_id),
            "scheduled_at": slot.scheduled_at.isoformat() if slot.scheduled_at else None,
            "style_hint": slot.style_hint,
            "reason": slot.reason,
        }
    return payload


async def _prepare_round(
    session: AsyncSession, goal: Goal, round_number: int
) -> int:
    """在 goal 上开 1 轮。

    v0.7+ 主路径：调 ``round_allocator.allocate`` 拿 N 个 slot，每台 1 个 run
    （同主题 + 风格轮换 + 时间错开 + 预分配 device/account）。

    降级：未注入 round_allocator / 0 active device / 活跃窗外 → 跑 N 份占位 brief
    （不带 preassigned_slot，回退 SCHEDULE 节点 ``choose_slot`` 随机路径）。
    """
    target = dict(goal.target or {})

    # 拉历史经验（v0.7 Phase 3：learning_prompt，KB 里有 strategy_card/rule 时带入 prompt）
    learnings_text = ""
    try:
        from .learning_prompt import fetch_relevant_learnings

        learnings_text = await fetch_relevant_learnings(
            session,
            theme=str(target.get("theme", "")),
            audience=target.get("audience"),
        )
    except Exception:
        logger.exception("orchestrator.learnings_fetch_failed", goal_id=str(goal.id))

    # 主路径：拉 N 个 slot
    slots, _n = await _allocate_round_slots(session, goal)

    created = 0
    if slots:
        # 每台设备 1 个 run，同主题 + style_hint 轮换 + 时间错开
        for slot in slots:
            payload = _build_run_payload(goal, target, round_number, slot, learnings_text=learnings_text)
            run = AgentRun(
                goal_id=goal.id,
                current_state="IDLE",
                payload=payload,
                round_number=round_number,
                status="running",
            )
            session.add(run)
            created += 1
    else:
        # 降级路径：N 份占位 brief（旧随机 choose_slot 路径）
        n = _count_target_for_round(goal)
        for _ in range(n):
            payload = _build_run_payload(goal, target, round_number, slot=None, learnings_text=learnings_text)
            run = AgentRun(
                goal_id=goal.id,
                current_state="IDLE",
                payload=payload,
                round_number=round_number,
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


async def _check_runs_done(
    session: AsyncSession, goal_id: uuid.UUID, round_number: int
) -> bool:
    """本轮所有 run 都不在 running → 可以进 MONITORING。

    只看当前轮次；旧轮残留的 running 不会卡住新轮。
    v0.7+ 第 2 期：round_number 已提升到一等列（迁移 011），
    走 idx_agent_runs_goal_round_status 复合索引命中。
    """
    stmt = select(func.count(AgentRun.id)).where(
        AgentRun.goal_id == goal_id,
        AgentRun.round_number == round_number,
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

    看当前轮次（v0.7+ 第 2 期 round_number 已是一等列）；note 解析三层 fallback：
      1) notes.run_id == run.id（首选，DRAFT/PUBLISH 会写）
      2) run.payload['note_id']（早期没接 run_id 时的过渡）
      3) 时间窗回退（5 min，附 WARNING，留给老数据）
    """
    runs_stmt = select(AgentRun).where(
        AgentRun.goal_id == goal_id,
        AgentRun.round_number == round_number,
    )
    runs = (await session.execute(runs_stmt)).scalars().all()

    total_views = 0
    total_likes = 0
    total_collects = 0
    total_comments = 0
    notes_count = 0
    per_note: list[dict[str, Any]] = []

    for run in runs:
        note = None

        # 1) 首选：notes.run_id 直查（新写入路径）
        if run.id is not None:
            note = (
                await session.execute(
                    select(Note).where(Note.run_id == run.id)
                )
            ).scalars().first()

        # 2) 过渡：run.payload['note_id']（DRAFT 节点生成的 uuid，比时间窗更准）
        if note is None:
            note_id_val = None
            if isinstance(run.payload, dict):
                note_id_val = run.payload.get("note_id")
            if note_id_val:
                try:
                    from uuid import UUID as _UUID

                    note = (
                        await session.execute(
                            select(Note).where(Note.id == _UUID(str(note_id_val)))
                        )
                    ).scalars().first()
                except (ValueError, TypeError):
                    note = None

        # 3) 最后回退：窄时间窗（5 min，仅老数据；附 WARNING 便于后续清理）
        if note is None:
            from datetime import timedelta

            logger.warning(
                "orchestrator.note_resolve_fallback_window",
                run_id=str(run.id),
                reason="no notes.run_id or payload.note_id match",
            )
            window = timedelta(minutes=5)
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

    flat: list[dict[str, Any]] = [
        {
            "note_id": row["note_id"],
            "title": row["title"],
            "views": row["views"],
            "likes": row["likes"],
            "collects": row["collects"],
            "comments": row["comments"],
            "follows_gained": 0,  # 旧 NoteMetric 行未必有 follows_gained
        }
        for row in per_note
    ]
    # Phase 2a #4：三维 KPI（曝光 / 互动 / 转化）。前端按维度直接渲染。
    from .kpi import compute_dim_kpi

    dimensions = compute_dim_kpi(flat)

    return {
        "total_views": total_views,
        "total_likes": total_likes,
        "total_collects": total_collects,
        "total_comments": total_comments,
        "notes_count": notes_count,
        "per_note": per_note,
        "dimensions": dimensions,
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


async def _load_round_kpi(
    session: AsyncSession, goal_id: uuid.UUID, round_number: int
) -> dict[str, Any]:
    """从 ``goal_rounds.kpi_summary`` 读本轮 KPI（SUMMARIZING/DECIDING 用，避免重算）。

    MONITORING 阶段负责写入；SUMMARIZING/DECIDING 直接读。读不到时返回空 dict
    （等同"本轮没数据"）。
    """
    stmt = select(GoalRound).where(
        GoalRound.goal_id == goal_id, GoalRound.round_number == round_number
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None or row.kpi_summary is None:
        return {}
    return dict(row.kpi_summary)


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
        # Phase 4 #3：自动发布爆款模板，关闭学习循环
        kb_docs = await summarize_goal_to_kb(
            session, embedder, goal.id, auto_publish=True
        )
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

    Phase 2a #4 真正接入：多维 KPI 取代单 likes 阈值。
    优先看 ``kpi['dimensions']``（新格式），缺失时回退 ``kpi['total_likes']``
    单字段（向后兼容老 kpi_summary）。
    """
    # 1) deadline 到了 → 收工
    if goal.deadline is not None and _utcnow() >= goal.deadline:
        return False, f"deadline reached: {goal.deadline.isoformat()}"
    # 2) 多维 KPI 达成 → 收工
    target_likes = (
        getattr(goal, "target_likes", DEFAULT_KPI_LIKES_TARGET)
        or DEFAULT_KPI_LIKES_TARGET
    )
    from .kpi import should_continue as _should_continue_kpi

    dimensions = kpi.get("dimensions") if isinstance(kpi, dict) else None
    if dimensions:
        # 三维判断（likes / views / engagement 任一达标即收工）
        cont, reason = _should_continue_kpi(
            dimensions, target_likes=target_likes
        )
        if not cont:
            return False, reason
    else:
        # 老 kpi_summary 格式兜底：只看 likes
        if kpi.get("total_likes", 0) >= target_likes:
            return False, (
                f"KPI achieved: {kpi.get('total_likes')}/{target_likes} likes"
            )
    # 3) 跑满 max_rounds → 收工
    if goal.current_round >= goal.max_rounds:
        return False, f"max_rounds reached: {goal.max_rounds}"
    # 4) 否则续跑
    return True, f"continue: round {goal.current_round + 1}/{goal.max_rounds}"


# ---------------------------------------------------------------------------
# 推进 1 步：把 goal 从当前 phase 推到下一个 phase
# ---------------------------------------------------------------------------


async def _safe_notify(code: str, payload: dict[str, Any]) -> None:
    """Phase 1 反向反馈：调 notifier 但绝不抛异常（与主流程解耦）。

    失败只记日志，不挡 phase 推进。notifier 内部本身已经 try/except，
    这里再包一层防御，避免 get_services() 未初始化等场景。
    """
    try:
        notifier = get_services().notifier
        await notifier(code, payload)
    except Exception:
        logger.warning(
            "orchestrator.notify_safe_failed", code=code, exc_info=True
        )


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
        # 拆任务：拉 N 个 slot → 写 N 条 run + 1 条 goal_rounds
        created = await _prepare_round(session, goal, goal.current_round)
        await _set_phase(session, goal, PHASE_EXECUTING)
        action = f"prepared {created} runs"
        await session.commit()
        # Phase 1：通知"本轮已派出 N 个 run"
        if created > 0:
            await _safe_notify(
                "goal.round.prepared",
                {
                    "goal_id": str(goal.id),
                    "round_number": goal.current_round,
                    "runs_created": created,
                    "eta_min": 5,
                },
            )
        return OrchestratorResult(
            goal_id=goal.id,
            phase_before=phase_before,
            phase_after=PHASE_EXECUTING,
            round_number=goal.current_round,
            notes_created_this_round=created,
            action=action,
        )

    if goal.phase == PHASE_EXECUTING:
        # 等所有本轮 run 跑完
        done = await _check_runs_done(session, goal.id, goal.current_round)
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
        # Phase 1：通知"本轮 KPI 已收齐"
        await _safe_notify(
            "goal.round.monitored",
            {
                "goal_id": str(goal.id),
                "round_number": goal.current_round,
                "notes_count": kpi.get("notes_count", 0),
            },
        )
        return OrchestratorResult(
            goal_id=goal.id,
            phase_before=phase_before,
            phase_after=PHASE_SUMMARIZING,
            round_number=goal.current_round,
            notes_created_this_round=kpi.get("notes_count", 0),
            action=f"monitored: {kpi.get('notes_count', 0)} notes",
        )

    if goal.phase == PHASE_SUMMARIZING:
        # 直接从 goal_rounds.kpi_summary 读，不再重算
        kpi = await _load_round_kpi(session, goal.id, goal.current_round)
        await _summarize_round(session, goal, goal.current_round, kpi)
        await _set_phase(session, goal, PHASE_DECIDING)
        await session.commit()
        # Phase 1：通知"本轮已总结完，进决策"
        await _safe_notify(
            "goal.round.decided",
            {"goal_id": str(goal.id), "round_number": goal.current_round},
        )
        return OrchestratorResult(
            goal_id=goal.id,
            phase_before=phase_before,
            phase_after=PHASE_DECIDING,
            round_number=goal.current_round,
            notes_created_this_round=0,
            action="summarized",
        )

    if goal.phase == PHASE_DECIDING:
        # 直接从 goal_rounds.kpi_summary 读
        kpi = await _load_round_kpi(session, goal.id, goal.current_round)
        cont, reason = _should_continue(goal, kpi)
        if cont:
            goal.current_round += 1
            goal.learning_summary = (
                (goal.learning_summary or "")
                + f"\n[{reason}] → 第 {goal.current_round} 轮"
            )
            await _set_phase(session, goal, PHASE_PREPARING)
            await session.commit()
            # Phase 1：通知"决策：继续下一轮"
            await _safe_notify(
                "goal.round.decided.continue",
                {
                    "goal_id": str(goal.id),
                    "round_number": goal.current_round,
                    "next_round": goal.current_round,
                    "reason": reason,
                    "eta_min": 5,
                },
            )
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
            # Phase 1：通知"目标完成/收工"
            await _safe_notify(
                "goal.round.decided.done",
                {
                    "goal_id": str(goal.id),
                    "round_number": goal.current_round,
                    "total_rounds": goal.current_round,
                    "reason": reason,
                },
            )
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
    "DEFAULT_MAX_ROUND_FANOUT",
    "DEFAULT_STAGGER_MINUTES",
    "DEFAULT_KPI_LIKES_TARGET",
    "advance_goal",
]
