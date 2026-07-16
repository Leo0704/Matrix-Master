"""chat 路由的 dispatch 层：把 LLM 输出的 {intent, args} → 数据查询 / 写操作 / 预览。

chat 在进程内，直接 import ORM 操作，不走 HTTP 调自己。

5 类工具：
  - ask_data: 只读数据查询（summary / weekly_top / running / single）
  - diagnose: 数据诊断（拉两轮 KPI diff + 二次 LLM 归因 + KB 检索）— 第 3 期
  - preview_change: 调参数预览（不写库，返 diff 给前端展示）— 第 2 期
  - apply_change: 已确认执行（重查 DB + 写库）— 第 2 期
  - browse_kb: KB 经验卡浏览（按 type/is_published/time）— 第 3 期

写操作流程：
  1) 用户说"暂停 X" → LLM 输出 intent=preview_change + args.filter + args.changes
  2) chat_tools.preview_change：_resolve_goal_filter 拿 goal → 构造 before/after diff → 返 preview（requires_confirmation=True）
  3) chat 路由生成 confirmation_token，把 args 存 _CONFIRMATION_STORE
  4) 前端展示预览 + 确认/取消按钮
  5) 用户点确认 → 前端发 /confirm <token> → 路由查 token 拿到原 args → chat_tools.apply_change
  6) apply_change 重新查 DB（preview 后用户可能等 10 分钟）→ 调 _do_change 写库
  7) 部分失败 → action.type=partial_success
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import Goal as GoalORM
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


# 批量操作硬上限（避免误伤）
CHAT_BATCH_LIMIT = 50


def _ago(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------------------------------------------------------------------------
# 共享 helper
# ---------------------------------------------------------------------------


async def _resolve_goal_filter(
    session: AsyncSession,
    filter_args: dict[str, Any],
    *,
    business_id: uuid.UUID,
) -> list[GoalORM]:
    """把 LLM 的模糊描述反查为 GoalORM 列表。

    v0.7+：强制按 business_id 过滤（chat 鉴权链路核心）。

    接受的 filter_args 字段（多个 AND 关系）：
      - goal_id: uuid（精确）
      - theme_keyword: str（target.theme LIKE）
      - product_category: str（target.product_category 精确）
      - type: str（Goal.type 精确）
      - status: str（默认 'active'）
    """
    if not filter_args:
        return []

    # v0.7+：强制业务过滤（chat_tools.py 是 chat 鉴权链路最后一关）
    stmt = (
        select(GoalORM)
        .where(GoalORM.deleted_at.is_(None))
        .where(GoalORM.business_id == business_id)
    )

    goal_id_str = filter_args.get("goal_id")
    if goal_id_str:
        try:
            stmt = stmt.where(GoalORM.id == uuid.UUID(str(goal_id_str)))
        except (ValueError, TypeError):
            logger.warning("chat_tools.invalid_goal_id", value=goal_id_str)
            return []

    theme_kw = filter_args.get("theme_keyword")
    if theme_kw:
        stmt = stmt.where(GoalORM.target["theme"].astext.ilike(f"%{theme_kw}%"))

    product_category = filter_args.get("product_category")
    if product_category:
        stmt = stmt.where(GoalORM.target["product_category"].astext == product_category)

    type_val = filter_args.get("type")
    if type_val:
        stmt = stmt.where(GoalORM.type == type_val)

    status_val = filter_args.get("status", "active")
    if status_val:
        stmt = stmt.where(GoalORM.status == status_val)

    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


# GoalUpdate schema 允许的字段（与 matrix.api.schemas.goal.GoalUpdate 对齐）
_ALLOWED_CHANGE_FIELDS = {
    "type",
    "target",
    "deadline",
    "target_likes",
    "notes_per_round",
    "max_rounds",
    "status",
}


def _validate_change_field(field: str, value: Any) -> None:
    """校验单字段合法性。复用 GoalUpdate 的 Pydantic 校验规则。"""
    if field not in _ALLOWED_CHANGE_FIELDS:
        raise ValueError(
            f"field '{field}' not allowed (allowed: {sorted(_ALLOWED_CHANGE_FIELDS)})"
        )

    # 数值字段范围（与 schemas/goal.py:103-105 一致）
    if field == "target_likes":
        if not isinstance(value, int) or not (1 <= value <= 1_000_000):
            raise ValueError(f"target_likes must be int in [1, 1_000_000]")
    elif field == "notes_per_round":
        if not isinstance(value, int) or not (1 <= value <= 20):
            raise ValueError(f"notes_per_round must be int in [1, 20]")
    elif field == "max_rounds":
        if not isinstance(value, int) or not (1 <= value <= 20):
            raise ValueError(f"max_rounds must be int in [1, 20]")
    elif field == "status":
        allowed_status = {"active", "achieved", "failed", "cancelled"}
        if value not in allowed_status:
            raise ValueError(f"status must be one of {sorted(allowed_status)}")


def _do_change(
    goal: GoalORM,
    field: str,
    to_value: Any,
    *,
    operator_business_id: uuid.UUID,
) -> Any:
    """preview 和 apply 共享的写操作。返回旧值（用于 diff 展示）。

    v0.7+：校验 goal.business_id == operator_business_id，跨业务直接抛错。
    """
    _validate_change_field(field, to_value)
    # v0.7+ 业务鉴权：goal 所属业务必须与操作者业务一致
    if goal.business_id != operator_business_id:
        raise ValueError(
            f"cross_business_modification_forbidden: "
            f"goal {goal.id} belongs to business {goal.business_id}, "
            f"operator business {operator_business_id}"
        )
    old = getattr(goal, field, None)
    setattr(goal, field, to_value)
    return old


# ---------------------------------------------------------------------------
# 工具函数实现
# ---------------------------------------------------------------------------


async def ask_data(
    session: AsyncSession,
    args: dict[str, Any],
    *,
    business_id: uuid.UUID,
) -> dict[str, Any]:
    """只读数据查询。子命令：summary / weekly_top / running / single。

    返回 {"requires_confirmation": False, "payload": {...}}
    """
    subcommand = str(args.get("subcommand") or "summary").lower()
    limit = int(args.get("limit") or 5)

    if subcommand == "weekly_top":
        from matrix.db.models import GoalRound as GoalRoundORM

        cutoff = _ago(7)
        stmt = (
            select(GoalORM, GoalRoundORM)
            .join(GoalRoundORM, GoalRoundORM.goal_id == GoalORM.id)
            .where(GoalORM.deleted_at.is_(None))
            .where(GoalORM.business_id == business_id)  # v0.7+ 业务过滤
            .where(GoalRoundORM.started_at >= cutoff)
            .order_by(GoalRoundORM.total_likes.desc())
            .limit(limit)
        )
        rows = (await session.execute(stmt)).all()
        items = [
            {
                "goal_id": str(g.id),
                "type": g.type,
                "theme": (g.target or {}).get("theme", ""),
                "round_number": r.round_number,
                "total_views": r.total_views or 0,
                "total_likes": r.total_likes or 0,
                "started_at": r.started_at.isoformat() if r.started_at else None,
            }
            for g, r in rows
        ]
        return {
            "requires_confirmation": False,
            "payload": {"subcommand": "weekly_top", "items": items, "total": len(items)},
        }

    if subcommand == "running":
        stmt = (
            select(GoalORM)
            .where(
                GoalORM.deleted_at.is_(None),
                GoalORM.status == "active",
                GoalORM.business_id == business_id,  # v0.7+ 业务过滤
            )
            .order_by(GoalORM.created_at.desc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        items = [
            {
                "goal_id": str(g.id),
                "type": g.type,
                "theme": (g.target or {}).get("theme", ""),
                "phase": g.phase,
                "current_round": g.current_round,
                "max_rounds": g.max_rounds,
            }
            for g in rows
        ]
        return {
            "requires_confirmation": False,
            "payload": {"subcommand": "running", "items": items, "total": len(items)},
        }

    if subcommand == "single":
        goal_id_str = args.get("goal_id")
        if not goal_id_str:
            return {
                "requires_confirmation": False,
                "payload": {"subcommand": "single", "error": "goal_id is required"},
            }
        try:
            g = await session.get(GoalORM, uuid.UUID(str(goal_id_str)))
        except (ValueError, TypeError):
            return {
                "requires_confirmation": False,
                "payload": {"subcommand": "single", "error": "invalid goal_id"},
            }
        if g is None or g.deleted_at is not None:
            return {
                "requires_confirmation": False,
                "payload": {"subcommand": "single", "error": "goal not found"},
            }
        # v0.7+ 跨业务拒绝：goal 必须在当前业务下
        if g.business_id != business_id:
            return {
                "requires_confirmation": False,
                "payload": {"subcommand": "single", "error": "goal_not_in_business"},
            }
        goals = [g]
        return {
            "requires_confirmation": False,
            "payload": {
                "subcommand": "single",
                "item": {
                    "goal_id": str(g.id),
                    "type": g.type,
                    "theme": (g.target or {}).get("theme", ""),
                    "status": g.status,
                    "phase": g.phase,
                    "current_round": g.current_round,
                    "max_rounds": g.max_rounds,
                    "target_likes": g.target_likes,
                    "notes_per_round": g.notes_per_round,
                },
            },
        }

    # 默认：summary
    stmt = (
        select(GoalORM)
        .where(
            GoalORM.deleted_at.is_(None),
            GoalORM.business_id == business_id,  # v0.7+ 业务过滤
        )
        .order_by(GoalORM.created_at.desc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    items = [
        {
            "goal_id": str(g.id),
            "type": g.type,
            "theme": (g.target or {}).get("theme", ""),
            "status": g.status,
            "phase": g.phase,
            "current_round": g.current_round,
            "max_rounds": g.max_rounds,
        }
        for g in rows
    ]
    return {
        "requires_confirmation": False,
        "payload": {"subcommand": "summary", "items": items, "total": len(items)},
    }


async def diagnose(
    session: AsyncSession,
    args: dict[str, Any],
    *,
    business_id: uuid.UUID,
) -> dict[str, Any]:
    """诊断：拉最近两轮 KPI diff + KB 召回 + 二次 LLM 归因。

    必须能唯一定位一个 goal：goal_id / theme_keyword / product_category 三选一。

    返回：
      {
        "requires_confirmation": False,
        "payload": {
          "goal_id": str,
          "theme": str,
          "rounds": [{round_number, total_views, total_likes, notes_created, started_at, ended_at}, ...],
          "kpi_diff": {"views": float, "likes": float, "comments": float, "interpretation": str},
          "llm_attribution": str | None,
          "related_strategy_cards": [{"doc_id": str, "title": str, "snippet": str}],
        }
      }
    """
    from matrix.db.models import GoalRound as GoalRoundORM
    from matrix.db.models import KbDocument as KbDocumentORM

    # 1) 解析 filter → 唯一 goal
    goals = await _resolve_goal_filter(session, args, business_id=business_id)
    if len(goals) == 0:
        return {
            "requires_confirmation": False,
            "payload": {"error": "no_goal_found", "filter": args},
        }
    if len(goals) > 1:
        return {
            "requires_confirmation": False,
            "payload": {
                "error": "multiple_goals_match",
                "matched_count": len(goals),
                "hint": "请指定 goal_id 或更精确的主题关键词",
            },
        }
    goal = goals[0]

    # 2) 拉所有轮次按 round_number 升序
    stmt = (
        select(GoalRoundORM)
        .where(GoalRoundORM.goal_id == goal.id)
        .order_by(GoalRoundORM.round_number.asc())
    )
    rounds = list((await session.execute(stmt)).scalars().all())

    rounds_payload = [
        {
            "round_number": r.round_number,
            "total_views": r.total_views or 0,
            "total_likes": r.total_likes or 0,
            "notes_created": r.notes_created or 0,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        }
        for r in rounds
    ]

    # 3) 计算最近两轮 KPI diff（只算有至少 2 轮的情况）
    kpi_diff: dict[str, Any] = {"interpretation": "数据不足（需要至少 2 轮）"}
    if len(rounds) >= 2:
        prev = rounds[-2]
        curr = rounds[-1]
        delta_views = (curr.total_views or 0) - (prev.total_views or 0)
        delta_likes = (curr.total_likes or 0) - (prev.total_likes or 0)
        likes_pct = (
            (delta_likes / max(prev.total_likes or 1, 1)) * 100
            if prev.total_likes
            else 0.0
        )
        interpretation = (
            f"浏览 Δ={delta_views:+d}，点赞 Δ={delta_likes:+d}（{likes_pct:+.1f}%）"
        )
        kpi_diff = {
            "views": delta_views,
            "likes": delta_likes,
            "likes_pct": round(likes_pct, 2),
            "interpretation": interpretation,
            "prev_round": prev.round_number,
            "curr_round": curr.round_number,
        }

    # 4) KB 检索相关 strategy_card
    related: list[dict[str, Any]] = []
    try:
        kb_stmt = (
            select(KbDocumentORM)
            .where(
                KbDocumentORM.type == "strategy_card",
                KbDocumentORM.is_published.is_(True),
                KbDocumentORM.deleted_at.is_(None),
                KbDocumentORM.business_id == business_id,  # v0.7+ 业务过滤
            )
            .order_by(KbDocumentORM.updated_at.desc())
            .limit(50)
        )
        kb_rows = list((await session.execute(kb_stmt)).scalars().all())
        theme = (goal.target or {}).get("theme", "")
        # 简单匹配：title 或 content 含主题关键词
        theme_kw = theme[:6] if theme else ""
        for doc in kb_rows:
            haystack = f"{doc.title or ''} {doc.content or ''}"
            if theme_kw and theme_kw in haystack:
                related.append(
                    {
                        "doc_id": str(doc.id),
                        "title": doc.title or "",
                        "snippet": (doc.content or "")[:150],
                    }
                )
                if len(related) >= 3:
                    break
    except Exception:
        logger.exception("diagnose.kb_search failed")

    # 5) 二次 LLM 归因：数据够且 LLM 可达时调用
    llm_attribution: Optional[str] = None
    if len(rounds) >= 2 and kpi_diff.get("likes_pct", 0) != 0:
        try:
            from matrix.agent.prompts import ANALYZE_SYSTEM, ANALYZE_USER

            from matrix.llm.router import get_default_client
            from matrix.config import get_settings

            user_prompt = ANALYZE_USER.format(
                title=theme or "(无主题)",
                content=f"诊断 goal {goal.id}：{kpi_diff['interpretation']}",
                tags=[],
                views=curr.total_views or 0,
                likes=curr.total_likes or 0,
                collects=0,
                comments=0,
                follows_gained=0,
                persona_style="",
                rules="",
            )
            settings = get_settings()
            client = get_default_client()
            resp = await client.complete(
                user_prompt,
                model=settings.matrix_llm_model or "MiniMax-M3",
                max_tokens=300,
                temperature=0.3,
                system=ANALYZE_SYSTEM,
                call_type="diagnose",
            )
            # 解析 review_text 字段（如果 LLM 输出 JSON）
            try:
                import json
                parsed = json.loads(resp.text)
                llm_attribution = str(parsed.get("review_text", "")).strip() or resp.text[:200]
            except Exception:
                # LLM 没输出 JSON，直接拿原文前 200 字
                llm_attribution = resp.text[:200].strip()
        except Exception:
            logger.exception("diagnose.llm_attribution failed")
            llm_attribution = None  # 数据 + 表格仍返回，UI 显示"归因暂不可用"

    return {
        "requires_confirmation": False,
        "payload": {
            "goal_id": str(goal.id),
            "theme": (goal.target or {}).get("theme", ""),
            "type": goal.type,
            "phase": goal.phase,
            "rounds": rounds_payload,
            "kpi_diff": kpi_diff,
            "llm_attribution": llm_attribution,
            "related_strategy_cards": related,
        },
    }


async def preview_change(
    session: AsyncSession,
    args: dict[str, Any],
    *,
    business_id: uuid.UUID,
    operator_business_id: uuid.UUID,
) -> dict[str, Any]:
    """调参数预览。

    必须有 filter + changes（路由层已校验，这里再校验一次兜底）。
    不写库，构造 before/after diff 后返 preview 让前端展示。

    返回：
      {
        "requires_confirmation": True,
        "payload": {
          "matched": [{goal_id, theme, current_status, current_max_rounds, ...}, ...],
          "diffs": [{goal_id, field, from, to}, ...],
          "action_summary": str,
        }
      }
    """
    filter_args = args.get("filter") or {}
    changes = args.get("changes") or []

    if not isinstance(filter_args, dict) or not isinstance(changes, list) or not changes:
        return {
            "requires_confirmation": False,
            "payload": {
                "error": "missing_filter_or_changes",
                "filter": filter_args,
                "changes": changes,
            },
        }

    goals = await _resolve_goal_filter(session, filter_args, business_id=business_id)
    if not goals:
        return {
            "requires_confirmation": False,
            "payload": {
                "error": "no_goal_match_filter",
                "filter": filter_args,
            },
        }

    # 构造 matched 列表（让前端展示"将影响哪几个 goal"）
    matched = [
        {
            "goal_id": str(g.id),
            "theme": (g.target or {}).get("theme", ""),
            "type": g.type,
            "current_status": g.status,
            "current_max_rounds": g.max_rounds,
            "current_target_likes": g.target_likes,
            "current_notes_per_round": g.notes_per_round,
        }
        for g in goals
    ]

    # 构造 diffs（每个 goal × 每个 change）
    diffs = []
    for g in goals:
        for change in changes:
            if not isinstance(change, dict):
                continue
            field = str(change.get("field", ""))
            to_value = change.get("to")
            try:
                _validate_change_field(field, to_value)
            except ValueError as e:
                return {
                    "requires_confirmation": False,
                    "payload": {
                        "error": "invalid_change",
                        "goal_id": str(g.id),
                        "field": field,
                        "to": to_value,
                        "reason": str(e),
                    },
                }
            old_value = getattr(g, field, None)
            diffs.append(
                {
                    "goal_id": str(g.id),
                    "field": field,
                    "from": old_value,
                    "to": to_value,
                }
            )

    # 构造人读 summary（"暂停 N 个 goal" / "把 X 个 goal 的 max_rounds 改成 5"）
    field_summary = ", ".join(
        f"{c.get('field')}={c.get('to')!r}" for c in changes if isinstance(c, dict)
    )
    action_summary = f"将影响 {len(goals)} 个 goal：{field_summary}"

    return {
        "requires_confirmation": True,
        "payload": {
            "matched": matched,
            "diffs": diffs,
            "action_summary": action_summary,
            "filter": filter_args,
            "changes": changes,
        },
    }


async def apply_change(
    session: AsyncSession,
    args: dict[str, Any],
    *,
    business_id: uuid.UUID,
    operator_business_id: uuid.UUID,
) -> dict[str, Any]:
    """已确认执行：重新查 DB → 调 _do_change → 部分失败返 partial_success。

    args 必含 confirmation_token（路由层已经校验过）；filter + changes 由
    preview_change 时存进 _CONFIRMATION_STORE 的 args 透传。

    返回：
      - 全成功：{"requires_confirmation": False, "payload": {"succeeded": [goal_id...], "failed": []}}
      - 部分失败：{"requires_confirmation": False, "payload": {"succeeded": [...], "failed": [{"goal_id":..., "error":...}]}}
    """
    # 校验 confirmation_token 在 args 里（路由层已经校验过，这里再校验一次）
    if not args.get("confirmation_token"):
        return {
            "requires_confirmation": False,
            "payload": {"error": "missing_confirmation_token"},
        }

    filter_args = args.get("filter") or {}
    changes = args.get("changes") or []

    if not isinstance(filter_args, dict) or not isinstance(changes, list) or not changes:
        return {
            "requires_confirmation": False,
            "payload": {"error": "missing_filter_or_changes"},
        }

    # 重新查 DB（preview 后用户可能等了 10 分钟，goal 状态可能已变）
    goals = await _resolve_goal_filter(session, filter_args, business_id=business_id)
    if not goals:
        return {
            "requires_confirmation": False,
            "payload": {"error": "no_goal_match_filter_at_apply_time"},
        }

    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []

    for g in goals:
        try:
            for change in changes:
                if not isinstance(change, dict):
                    continue
                field = str(change.get("field", ""))
                to_value = change.get("to")
                _do_change(g, field, to_value, operator_business_id=operator_business_id)
            succeeded.append(str(g.id))
        except Exception as e:
            logger.exception("apply_change.goal_failed", goal_id=str(g.id))
            failed.append({"goal_id": str(g.id), "error": str(e)})

    # 提交事务
    try:
        await session.commit()
    except Exception as e:
        logger.exception("apply_change.commit_failed")
        return {
            "requires_confirmation": False,
            "payload": {"error": "commit_failed", "detail": str(e)},
        }

    return {
        "requires_confirmation": False,
        "payload": {
            "succeeded": succeeded,
            "failed": failed,
            "total_succeeded": len(succeeded),
            "total_failed": len(failed),
        },
    }


async def browse_kb(
    session: AsyncSession,
    args: dict[str, Any],
    *,
    business_id: uuid.UUID,
) -> dict[str, Any]:
    """审 KB 经验卡。按 type / is_published / 时间窗过滤。

    返回：
      {
        "requires_confirmation": False,
        "payload": {
          "type": str,
          "days": int,
          "items": [{doc_id, title, content_preview, is_published, updated_at}],
          "total": int,
        }
      }
    """
    from matrix.db.models import KbDocument as KbDocumentORM

    doc_type = str(args.get("type") or "strategy_card")
    days = int(args.get("days") or 7)
    is_published_filter = args.get("is_published")  # bool | None

    cutoff = _ago(days)
    stmt = (
        select(KbDocumentORM)
        .where(
            KbDocumentORM.deleted_at.is_(None),
            KbDocumentORM.type == doc_type,
            KbDocumentORM.updated_at >= cutoff,
            KbDocumentORM.business_id == business_id,  # v0.7+ 业务过滤
        )
        .order_by(KbDocumentORM.updated_at.desc())
        .limit(50)
    )

    if is_published_filter is not None:
        stmt = stmt.where(KbDocumentORM.is_published.is_(bool(is_published_filter)))

    rows = list((await session.execute(stmt)).scalars().all())
    items = [
        {
            "doc_id": str(doc.id),
            "title": doc.title or "",
            "content_preview": (doc.content or "")[:200],
            "is_published": bool(doc.is_published),
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        }
        for doc in rows
    ]
    return {
        "requires_confirmation": False,
        "payload": {
            "type": doc_type,
            "days": days,
            "items": items,
            "total": len(items),
        },
    }


# ---------------------------------------------------------------------------
# Dispatch 注册表
# ---------------------------------------------------------------------------


CHAT_TOOL_DISPATCH = {
    "ask_data": ask_data,
    "diagnose": diagnose,
    "preview_change": preview_change,
    "apply_change": apply_change,
    "browse_kb": browse_kb,
}


# 每个工具的必填参数（路由层用做 missing_args 检查）
TOOL_REQUIRED_ARGS: dict[str, list[str]] = {
    "ask_data": [],
    "diagnose": [],
    "preview_change": ["filter", "changes"],
    "apply_change": ["confirmation_token"],
    "browse_kb": [],
}


__all__ = [
    "CHAT_BATCH_LIMIT",
    "CHAT_TOOL_DISPATCH",
    "TOOL_REQUIRED_ARGS",
    "ask_data",
    "diagnose",
    "preview_change",
    "apply_change",
    "browse_kb",
    "_resolve_goal_filter",
    "_do_change",
    "_validate_change_field",
]