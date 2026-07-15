"""RunManager：管理 ``agent_runs`` 与状态机执行。

API（async）：
    - :func:`create_run` — 新建 run，返回 run_id
    - :func:`start_run` — 启动一次状态机执行（直到 ALERT / 终态）
    - :func:`cancel_run` — 标记 cancelled
    - :func:`get_run_status` — 读取 run 行
    - :func:`resume_run` — 从 last checkpoint 续跑（注入 _alert_ack=True 等 flag）

依赖：
    - ``AgentRepository``（matrix.agent.repository）
    - ``StateMachine``（matrix.agent.state_machine）
    - ``AgentServices``（matrix.agent._services）
"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ._default_repository import DefaultAgentRepository
from ._services import AgentServices, set_services
from .repository import AgentRepository
from .state_machine import StateMachine
from .types import AgentState, RunStatus, State

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RunManager:
    """RunManager 持有 services / state_machine / repository。"""

    def __init__(
        self,
        *,
        services: AgentServices,
        state_machine: StateMachine | None = None,
        repository: AgentRepository | None = None,
    ) -> None:
        self.services = services
        set_services(services)
        self.sm = state_machine or StateMachine()
        self.repo = repository or DefaultAgentRepository()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_run(
        self,
        *,
        goal_id: UUID | None = None,
        goal_text: str | None = None,
        goal_type: str = "publish_note",
        entry: str = State.RESEARCH.value,
        brief: dict[str, Any] | None = None,
        interact_plan: list[dict[str, Any]] | None = None,
    ) -> UUID:
        """创建一条 run，返回 run_id。

        v0.6 新增 ``interact_plan``：发后流量互推目标列表。
        """
        run_id = uuid4()
        payload: dict[str, Any] = {
            "goal_text": goal_text or "",
            "goal_type": goal_type,
            "entry": entry,
        }
        if brief:
            payload["brief"] = brief
        if interact_plan:
            payload["interact_plan"] = interact_plan
        await self.repo.create_run(
            run_id=run_id,
            goal_id=goal_id,
            payload=payload,
            started_at=_utcnow(),
            current_state=State.IDLE.value,
            status=RunStatus.RUNNING.value,
        )
        # 起点 checkpoint
        await self.repo.write_checkpoint(
            run_id=run_id,
            from_state=State.IDLE.value,
            to_state=State.IDLE.value,
            payload={"created": True, "entry": entry},
        )
        logger.info("agent.run.created", run_id=run_id, entry=entry)
        return run_id

    async def start_run(self, run_id: UUID) -> AgentState:
        """驱动 state machine 一直跑到 END。

        ainvoke 抛未捕获异常时，保证 ``update_run`` 仍能把 run 标 FAILED，
        避免 ``runner._run_one`` 的 try/except 吞掉异常后 run 永远卡在 running。
        """
        run = await self.repo.get_run(run_id)
        if run is None:
            raise ValueError(f"agent_run not found: {run_id}")
        payload: dict[str, Any] = dict(run.payload or {})

        state: AgentState = {
            "run_id": run_id,
            "goal_id": run.goal_id,
            "current_state": State.IDLE.value,
            "revise_attempts": 0,
            "created_task_ids": [],
        }
        state["entry"] = payload.get("entry", State.RESEARCH.value)
        state["goal_text"] = payload.get("goal_text", "")
        state["goal_type"] = payload.get("goal_type", "publish_note")
        # 主题摘要：从 run.payload 注入（chat 路由写库时已塞入）
        brief = payload.get("brief")
        if isinstance(brief, dict) and brief:
            state["brief"] = brief
        # v0.7+ preassigned_slot：orchestrator._prepare_round 在 round 扇出时写入；
        # schedule_node 看到它就跳过 choose_slot 随机路径
        preassigned = payload.get("preassigned_slot")
        if isinstance(preassigned, dict) and preassigned:
            state["preassigned_slot"] = preassigned
        # v0.6 互动计划：发后流量互推（list[{note_id, kind, content_template?}])
        interact_plan = payload.get("interact_plan")
        if isinstance(interact_plan, list) and interact_plan:
            state["interact_plan"] = interact_plan
        else:
            state["interact_plan"] = []
        # v0.7 历史经验：orchestrator 拆任务时从 KB 拉取，DRAFT 节点拼 prompt 用
        learnings_text = payload.get("learnings_text")
        if isinstance(learnings_text, str) and learnings_text:
            state["learnings_text"] = learnings_text

        try:
            result = await self.sm.ainvoke(state)
        except Exception as exc:
            logger.exception("agent_run.crashed", run_id=run_id)
            # 兜底：把异常写回 DB，避免 run 永远卡在 running
            snapshot = {"code": "UNCAUGHT_CRASH", "message": str(exc)}
            await self.repo.update_run(
                run_id,
                status=RunStatus.FAILED.value,
                payload_merge={
                    "last_state": State.IDLE.value,
                    "last_error_snapshot": snapshot,
                },
                ended_at=_utcnow(),
            )
            # 把异常信息塞进 state 返回，调用方仍能看到
            return {
                **state,
                "current_state": State.IDLE.value,
                "last_error_snapshot": snapshot,
            }

        # run 状态判定：last_error_snapshot 非空 → 失败
        # （alert_node 会清 last_error，但留底在 last_error_snapshot；这是错误触发的可靠信号）
        ended_state = str(result.get("current_state", State.IDLE.value))
        is_failed = result.get("last_error_snapshot") is not None
        await self.repo.update_run(
            run_id,
            current_state=ended_state,
            status=RunStatus.FAILED.value
            if is_failed
            else RunStatus.SUCCESS.value,
            payload_merge={
                "last_state": ended_state,
                "last_error_snapshot": result.get("last_error_snapshot"),
            },
            ended_at=_utcnow(),
        )
        return result

    async def cancel_run(self, run_id: UUID) -> None:
        """标记 cancelled。"""
        await self.repo.update_run(
            run_id,
            status=RunStatus.CANCELLED.value,
            ended_at=_utcnow(),
        )
        await self.repo.write_checkpoint(
            run_id=run_id,
            from_state="__cancelled__",
            to_state=State.IDLE.value,
            payload={"reason": "cancelled_by_caller"},
        )
        logger.info("agent.run.cancelled", run_id=run_id)

    async def get_run_status(self, run_id: UUID) -> dict[str, Any] | None:
        """读取 run 行 + 最后一条 checkpoint。"""
        run = await self.repo.get_run(run_id)
        if run is None:
            return None
        last_cp = await self.repo.read_last_checkpoint(run_id)
        return {
            "id": str(run.id),
            "goal_id": str(run.goal_id) if run.goal_id else None,
            "current_state": run.current_state,
            "status": run.status,
            "payload": run.payload,
            "started_at": run.started_at,
            "ended_at": run.ended_at,
            "last_checkpoint": {
                "from_state": last_cp.from_state,
                "to_state": last_cp.to_state,
                "ts": last_cp.ts,
            }
            if last_cp
            else None,
        }

    # ------------------------------------------------------------------
    # 续跑
    # ------------------------------------------------------------------

    async def resume_run(
        self,
        run_id: UUID,
        *,
        alert_ack: bool = False,
    ) -> AgentState:
        """从最后一条 checkpoint 还原 state，继续推进。"""
        cp = await self.repo.read_last_checkpoint(run_id)
        run = await self.repo.get_run(run_id)
        if run is None:
            raise ValueError(f"agent_run not found: {run_id}")
        if run.status != RunStatus.RUNNING.value:
            raise ValueError(
                f"agent_run {run_id} status={run.status}; only running runs can be resumed"
            )
        payload: dict[str, Any] = cp.payload if cp else {}
        target_state = cp.to_state if cp else State.IDLE.value

        new_state: AgentState = dict(payload) if isinstance(payload, dict) else {}
        new_state.update(
            {
                "run_id": run_id,
                "goal_id": run.goal_id,
                "current_state": target_state,
                "_resume_from": target_state,
            }
        )
        # v0.6: resume 时补 interact_plan（从 run.payload 回填）
        if "interact_plan" not in new_state and isinstance(run.payload, dict):
            cp_plan = run.payload.get("interact_plan")
            if isinstance(cp_plan, list):
                new_state["interact_plan"] = cp_plan
        new_state.setdefault("interact_plan", [])
        # resume 时如 checkpoint payload 没 brief，从 run.payload 补
        if not new_state.get("brief") and isinstance(run.payload, dict):
            run_brief = run.payload.get("brief")
            if isinstance(run_brief, dict) and run_brief:
                new_state["brief"] = run_brief
        # resume 时补 preassigned_slot（SCHEDULE 失败 resume 也要按预分配走）
        if not new_state.get("preassigned_slot") and isinstance(run.payload, dict):
            run_pa = run.payload.get("preassigned_slot")
            if isinstance(run_pa, dict) and run_pa:
                new_state["preassigned_slot"] = run_pa
        # resume 时补 learnings_text（从 run.payload 回填）
        if not new_state.get("learnings_text") and isinstance(run.payload, dict):
            run_lt = run.payload.get("learnings_text")
            if isinstance(run_lt, str) and run_lt:
                new_state["learnings_text"] = run_lt
        if alert_ack:
            new_state["_alert_ack"] = True
        result = await self.sm.ainvoke(new_state)

        # run 状态判定：last_error_snapshot 非空 → 失败
        ended_state = str(result.get("current_state", State.IDLE.value))
        is_failed = result.get("last_error_snapshot") is not None
        await self.repo.update_run(
            run_id,
            current_state=ended_state,
            status=RunStatus.FAILED.value
            if is_failed
            else RunStatus.SUCCESS.value,
            payload_merge={
                "last_state": ended_state,
                "last_error_snapshot": result.get("last_error_snapshot"),
            },
            ended_at=_utcnow(),
        )
        return result


# ---------------------------------------------------------------------------
# 顶层便捷函数
# ---------------------------------------------------------------------------


_global_manager: RunManager | None = None


def init_manager(manager: RunManager) -> None:
    global _global_manager
    _global_manager = manager


def get_manager() -> RunManager:
    if _global_manager is None:
        raise RuntimeError("RunManager not initialized; call init_manager() first")
    return _global_manager


async def create_run(goal_id: UUID | None = None, **kwargs: Any) -> UUID:
    return await get_manager().create_run(goal_id=goal_id, **kwargs)


async def start_run(run_id: UUID) -> AgentState:
    return await get_manager().start_run(run_id)


async def cancel_run(run_id: UUID) -> None:
    await get_manager().cancel_run(run_id)


async def get_run_status(run_id: UUID) -> dict[str, Any] | None:
    return await get_manager().get_run_status(run_id)


async def resume_run(run_id: UUID, *, alert_ack: bool = False) -> AgentState:
    return await get_manager().resume_run(run_id, alert_ack=alert_ack)


__all__ = [
    "RunManager",
    "init_manager",
    "get_manager",
    "create_run",
    "start_run",
    "cancel_run",
    "get_run_status",
    "resume_run",
]
