"""LangGraph 状态机。

按 SDD §5.1 转移表实现 10 个主状态 + 2 个异常/中间态：

- 主状态: IDLE / RESEARCH / DRAFT / REVIEW / SCHEDULE / DISPATCH /
  PUBLISH / INTERACT / COLLECT / ANALYZE
- 异常态: ALERT（任意 fail 落入）、REVISE（review 失败回炉）

每个状态是一个独立节点函数（见 ``matrix.agent.nodes``），
状态间通过 ``add_conditional_edges`` 决定下一个状态。

run-time 流程：RunManager.create_run() → start_run() → state_machine.invoke(state)。

v0.6 新增 INTERACT：PUBLISH 成功后，若 ``interact_plan`` 非空则跳到 INTERACT
做发后流量互推（点赞 + 评论同类热门），结束后回 COLLECT 走回采。
"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from .guards import (
    GuardConfig,
    route_after_collect,
    route_after_dispatch,
    route_after_image_gen,  # v0.7 Phase 3
    route_after_interact,
    route_after_publish,
    route_after_research,
    route_after_review,
    route_after_revise,
    route_after_schedule,
    route_idle,
)
from .nodes import (
    alert_node,
    analyze_node,
    collect_node,
    dispatch_node,
    draft_node,
    image_gen_node,  # v0.7 Phase 3
    interact_node,
    publish_node,
    research_node,
    review_node,
    revise_node,
    schedule_node,
)
from .types import AgentState, State

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# StateMachine 包装
# ---------------------------------------------------------------------------


@dataclass
class StateMachine:
    """LangGraph StateGraph wrapper。暴露 ``compile()`` / ``ainvoke()``。"""

    cfg: GuardConfig = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.cfg is None:
            self.cfg = GuardConfig()
        self._graph = self._build()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def compile(self):
        """返回 langgraph CompiledStateGraph。"""
        return self._graph

    async def ainvoke(self, state: dict[str, Any] | AgentState) -> AgentState:
        """异步执行直到终态（ALERT / 任务完成）。

        节点抛未捕获异常时，记录日志后重新抛出——``RunManager.start_run``
        的外层 try/except 会负责把 run 标 FAILED。这里只做日志，避免
        ``update_run`` 被跳过造成 run 永远卡在 running。
        """
        compiled = self.compile()
        try:
            result = await compiled.ainvoke(dict(state))
        except Exception:
            logger.exception("state_machine.ainvoke_failed", state=state.get("current_state"))
            raise
        return result  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # 构图
    # ------------------------------------------------------------------

    def _build(self):
        """构造并返回编译后的 langgraph 图。"""
        g = StateGraph(AgentState)

        # 注册 13 个节点（11 主 + REVISE / ALERT）；每个节点用包装器自动
        # 在 partial update 末尾注入 ``current_state``，便于读 checkpoint。
        g.add_node(State.IDLE.value, _wrap(State.IDLE.value, _idle_entry))
        g.add_node(State.RESEARCH.value, _wrap(State.RESEARCH.value, research_node))
        g.add_node(State.DRAFT.value, _wrap(State.DRAFT.value, draft_node))
        g.add_node(
            State.IMAGE_GEN.value, _wrap(State.IMAGE_GEN.value, image_gen_node)
        )  # v0.7 Phase 3
        g.add_node(State.REVIEW.value, _wrap(State.REVIEW.value, review_node))
        g.add_node(State.REVISE.value, _wrap(State.REVISE.value, revise_node))
        g.add_node(State.SCHEDULE.value, _wrap(State.SCHEDULE.value, schedule_node))
        g.add_node(State.DISPATCH.value, _wrap(State.DISPATCH.value, dispatch_node))
        g.add_node(State.PUBLISH.value, _wrap(State.PUBLISH.value, publish_node))
        g.add_node(State.INTERACT.value, _wrap(State.INTERACT.value, interact_node))  # v0.6
        g.add_node(State.COLLECT.value, _wrap(State.COLLECT.value, collect_node))
        g.add_node(State.ANALYZE.value, _wrap(State.ANALYZE.value, analyze_node))
        g.add_node(State.ALERT.value, _wrap(State.ALERT.value, alert_node))

        cfg = self.cfg

        # START → IDLE 或 RESEARCH / ANALYZE 由 state['entry'] 决定
        g.add_conditional_edges(
            START,
            lambda s: route_idle(s, cfg),
            {
                State.RESEARCH.value: State.RESEARCH.value,
                State.ANALYZE.value: State.ANALYZE.value,
            },
        )

        # IDLE 是入口/汇聚点：从 ALERT / ANALYZE 回 IDLE 等下一次触发
        g.add_edge(State.IDLE.value, END)

        # RESEARCH → DRAFT | ALERT
        g.add_conditional_edges(
            State.RESEARCH.value,
            lambda s: route_after_research(s, cfg),
            {
                State.DRAFT.value: State.DRAFT.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # DRAFT → IMAGE_GEN（v0.7 Phase 3：生图插入 DRAFT 后）
        g.add_edge(State.DRAFT.value, State.IMAGE_GEN.value)

        # IMAGE_GEN → REVIEW | ALERT（按 fallback 决定走纯文还是 ALERT）
        g.add_conditional_edges(
            State.IMAGE_GEN.value,
            lambda s: route_after_image_gen(s, cfg),
            {
                State.REVIEW.value: State.REVIEW.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # REVIEW → SCHEDULE | REVISE | ALERT
        g.add_conditional_edges(
            State.REVIEW.value,
            lambda s: route_after_review(s, cfg),
            {
                State.SCHEDULE.value: State.SCHEDULE.value,
                State.REVISE.value: State.REVISE.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # REVISE → DRAFT | ALERT
        g.add_conditional_edges(
            State.REVISE.value,
            lambda s: route_after_revise(s, cfg),
            {
                State.DRAFT.value: State.DRAFT.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # SCHEDULE → DISPATCH | ALERT
        g.add_conditional_edges(
            State.SCHEDULE.value,
            lambda s: route_after_schedule(s, cfg),
            {
                State.DISPATCH.value: State.DISPATCH.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # DISPATCH → PUBLISH | ALERT
        g.add_conditional_edges(
            State.DISPATCH.value,
            lambda s: route_after_dispatch(s, cfg),
            {
                State.PUBLISH.value: State.PUBLISH.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # PUBLISH → INTERACT | IDLE | ALERT（v0.6：发后互动；
        # v0.7+ 时序修复：成功即收工，复盘由 24h 采集触发的独立 ANALYZE run 做）
        g.add_conditional_edges(
            State.PUBLISH.value,
            lambda s: route_after_publish(s, cfg),
            {
                State.INTERACT.value: State.INTERACT.value,
                State.IDLE.value: State.IDLE.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # INTERACT → IDLE | ALERT（v0.7+：互动完成即收工）
        g.add_conditional_edges(
            State.INTERACT.value,
            lambda s: route_after_interact(s, cfg),
            {
                State.IDLE.value: State.IDLE.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # COLLECT → ANALYZE | ALERT
        g.add_conditional_edges(
            State.COLLECT.value,
            lambda s: route_after_collect(s, cfg),
            {
                State.ANALYZE.value: State.ANALYZE.value,
                State.ALERT.value: State.ALERT.value,
            },
        )

        # ANALYZE → IDLE
        g.add_edge(State.ANALYZE.value, State.IDLE.value)

        # ALERT → IDLE（人工 ack 后才能动，未 ack 则停在 END 由 RunManager 续跑）
        g.add_conditional_edges(
            State.ALERT.value,
            lambda s: State.IDLE.value if s.get("_alert_ack") else END,
            {
                State.IDLE.value: State.IDLE.value,
                END: END,
            },
        )

        return g.compile()


# ---------------------------------------------------------------------------
# IDLE 占位节点（langgraph 要求每个 add_node 调用对应一个函数）
# ---------------------------------------------------------------------------


async def _idle_entry(state: AgentState) -> dict[str, Any]:
    """IDLE 是汇聚点 / 入口；本身不做任何计算，直接返回结束标记。

    实际触发由 RunManager 在 invoke 之前把 ``entry`` 设到 state。
    """
    return {
        "last_error": None,
    }


def _wrap(state_name: str, fn):
    """包装节点函数：自动注入 ``current_state``。"""

    async def _wrapped(state: AgentState) -> dict[str, Any]:
        result = await fn(state)
        if not isinstance(result, dict):
            result = {}
        result.setdefault("current_state", state_name)
        return result

    return _wrapped


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_state_machine(cfg: GuardConfig | None = None) -> StateMachine:
    """便捷工厂。"""
    return StateMachine(cfg=cfg or GuardConfig())


__all__ = [
    "StateMachine",
    "build_state_machine",
]
