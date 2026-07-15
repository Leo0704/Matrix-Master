"""多维 KPI 计算（Phase 2a #4）。

老板不要"likes 涨没涨"这一个数。要看：
- 曝光（exposure）：发了之后到底有多少人看到 = views
- 互动（engagement）：看到的人里多少动手 = likes + collects + comments + follows
- 转化（conversion）：动手的人里多少真的粉 = follows_gained / max(views, 1)
- 率（rates）：互动率、点赞率（每条稿 vs 总数）

返回结构是纯 dict，写到 ``GoalRound.kpi_summary`` 的 ``dimensions`` 子键。
前端可以直接按 key 渲染，老板点哪条稿看到底卡在哪一维。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def _safe_int(v: Any) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _per_note_row(metric: Mapping[str, Any]) -> dict[str, Any]:
    """把单条 NoteMetric 折叠成本轮的 per_note 行（+ 比率字段）。"""
    views = _safe_int(metric.get("views"))
    likes = _safe_int(metric.get("likes"))
    collects = _safe_int(metric.get("collects"))
    comments = _safe_int(metric.get("comments"))
    follows = _safe_int(metric.get("follows_gained"))
    engagement = likes + collects + comments + follows
    return {
        "note_id": str(metric.get("note_id") or ""),
        "title": str(metric.get("title") or ""),
        "views": views,
        "likes": likes,
        "collects": collects,
        "comments": comments,
        "follows_gained": follows,
        "engagement": engagement,
        # 比率用 0..1；views=0 时按 0 处理（不是 NaN），前端按 -1 显示"无曝光"
        "like_rate": (likes / views) if views > 0 else 0.0,
        "engage_rate": (engagement / views) if views > 0 else 0.0,
    }


def compute_dim_kpi(per_note_metrics: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """把一组本轮 NoteMetric 折叠成三维 KPI。

    输入：每个元素是 dict，含 ``views/likes/collects/comments/follows_gained``
    （以及可选 ``note_id/title`` 用于回显）。None 字段按 0 处理。

    返回 dict：
        exposure / engagement / conversion / rates / per_note
    """
    rows = [_per_note_row(m) for m in per_note_metrics]
    total_views = sum(r["views"] for r in rows)
    total_likes = sum(r["likes"] for r in rows)
    total_collects = sum(r["collects"] for r in rows)
    total_comments = sum(r["comments"] for r in rows)
    total_follows = sum(r["follows_gained"] for r in rows)
    total_engagement = total_likes + total_collects + total_comments + total_follows
    # 转化率用 views 做分母（"看到的人里多少粉"）
    conv = (total_follows / total_views) if total_views > 0 else 0.0
    return {
        "exposure": {"views": total_views, "notes": len(rows)},
        "engagement": {
            "likes": total_likes,
            "collects": total_collects,
            "comments": total_comments,
            "follows_gained": total_follows,
            "total": total_engagement,
        },
        "conversion": {"follows_gained": total_follows, "rate": conv},
        "rates": {
            "like_rate": (total_likes / total_views) if total_views > 0 else 0.0,
            "engage_rate": (total_engagement / total_views) if total_views > 0 else 0.0,
        },
        "per_note": rows,
    }


def should_continue(
    dimensions: Mapping[str, Any],
    *,
    target_likes: int,
    min_views: int = 0,
    min_engagement: int = 0,
) -> tuple[bool, str]:
    """多维 KPI 续跑判断：和 :func:`matrix.agent.orchestrator._should_continue`
    一样的语义 —— ``(True, reason)`` 表示"接着跑"，``(False, reason)`` 表示"收工"。

    三维阈值：likes / views / engagement 任何一个达到目标 → 收工（已经够格，
    再跑一轮只是浪费 LLM token）。全不达标 → 续跑（再调一轮试试）。

    默认 ``min_views=0, min_engagement=0`` → 等价于只看 likes（向后兼容）。
    调用方传更严的阈值时启用三维判断。
    """
    exposure = dimensions.get("exposure") or {}
    engagement = dimensions.get("engagement") or {}
    likes = int(engagement.get("likes", 0) or 0)
    views = int(exposure.get("views", 0) or 0)
    engage_total = int(engagement.get("total", 0) or 0)

    # 任一维达标 → 收工（False）
    if likes >= target_likes:
        return False, f"likes {likes}/{target_likes} met → stop"
    if min_views and views >= min_views:
        return False, f"views {views}/{min_views} met (likes lagging) → stop"
    if min_engagement and engage_total >= min_engagement:
        return False, (
            f"engagement {engage_total}/{min_engagement} met → stop"
        )
    # 全不达标 → 续跑（True）
    return True, (
        f"kpi short: likes {likes}/{target_likes}, "
        f"views {views}/{min_views or '-'}, "
        f"engagement {engage_total}/{min_engagement or '-'}"
    )


__all__ = ["compute_dim_kpi", "should_continue"]
