"""Goal orchestrator 测试（第 1 期）。

覆盖：
- advance_goal：PENDING→PREPARING→EXECUTING→MONITORING→SUMMARIZING→DECIDING→DONE 全链路
- 续跑路径（DECIDING 回 PREPARING + current_round+=1）
- 收工路径（max_rounds 到了 / KPI 达成 / deadline 到了）
- _should_continue 单元测试
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matrix.agent.orchestrator import (
    DEFAULT_KPI_LIKES_TARGET,
    NOTES_PER_ROUND,
    PHASE_DECIDING,
    PHASE_DONE,
    PHASE_EXECUTING,
    PHASE_MONITORING,
    PHASE_PENDING,
    PHASE_PREPARING,
    PHASE_SUMMARIZING,
    _should_continue,
    advance_goal,
)
from matrix.scheduler.round_slot_allocator import STYLE_ROTATION


def _make_goal(
    *,
    phase: str = PHASE_PENDING,
    current_round: int = 1,
    max_rounds: int = 3,
    deadline: datetime | None = None,
    status: str = "active",
    target: dict | None = None,
    target_likes: int = 500,
    notes_per_round: int = 3,
):
    g = SimpleNamespace()
    g.id = uuid.uuid4()
    g.phase = phase
    g.current_round = current_round
    g.max_rounds = max_rounds
    g.deadline = deadline
    g.status = status
    g.target = target or {"theme": "夏季男生穿搭", "audience": "18-25岁"}
    g.type = "publish_note"
    g.learning_summary = None
    g.target_likes = target_likes
    g.notes_per_round = notes_per_round
    # mutable state
    g._session = MagicMock()
    return g


def _mock_session_for_goal(goal):
    """让 session.get(Goal, goal.id) 返 goal。"""
    session = MagicMock()
    session.get = AsyncMock(return_value=goal)
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# _should_continue
# ---------------------------------------------------------------------------


class TestShouldContinue:
    def test_deadline_reached(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        g = _make_goal(deadline=past, current_round=1, max_rounds=3)
        cont, reason = _should_continue(g, {"total_likes": 0})
        assert cont is False
        assert "deadline" in reason

    def test_kpi_achieved(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        g = _make_goal(deadline=future, current_round=1, max_rounds=3)
        cont, reason = _should_continue(g, {"total_likes": DEFAULT_KPI_LIKES_TARGET})
        assert cont is False
        assert "KPI" in reason

    def test_kpi_uses_goal_target_likes(self):
        """goal.target_likes=100 时，50 赞不该收工，150 赞收工。"""
        future = datetime.now(timezone.utc) + timedelta(days=1)
        g = _make_goal(deadline=future, current_round=1, max_rounds=3, target_likes=100)
        # 50 < 100 → 续跑
        cont, _ = _should_continue(g, {"total_likes": 50})
        assert cont is True
        # 150 >= 100 → 收工
        cont, reason = _should_continue(g, {"total_likes": 150})
        assert cont is False
        assert "100" in reason  # 阈值写进 reason

    def test_max_rounds_reached(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        g = _make_goal(deadline=future, current_round=3, max_rounds=3)
        cont, reason = _should_continue(g, {"total_likes": 0})
        assert cont is False
        assert "max_rounds" in reason

    def test_continue(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        g = _make_goal(deadline=future, current_round=1, max_rounds=3)
        cont, reason = _should_continue(g, {"total_likes": 10})
        assert cont is True
        assert "continue" in reason


# ---------------------------------------------------------------------------
# advance_goal: 状态机推进
# ---------------------------------------------------------------------------


class TestAdvanceGoal:
    async def test_pending_to_preparing(self):
        g = _make_goal(phase=PHASE_PENDING)
        session = _mock_session_for_goal(g)
        result = await advance_goal(session, g)
        assert result.phase_after == PHASE_PREPARING
        assert g.phase == PHASE_PREPARING

    async def test_already_done_returns_none(self):
        g = _make_goal(phase=PHASE_DONE)
        result = await advance_goal(MagicMock(), g)
        assert result is None

    async def test_preparing_creates_runs(self):
        """无 round_allocator 时走降级路径：notes_per_round 份占位 brief + 1 条 goal_round。"""
        g = _make_goal(phase=PHASE_PREPARING, current_round=1)
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        # mock session.execute（fetch_relevant_learnings 内部用）
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=exec_result)
        # 无 round_allocator 注入 → 走 _count_target_for_round 降级路径
        from matrix.agent import _services as _svc
        prev = _svc._SERVICES
        _svc._SERVICES = None
        try:
            result = await advance_goal(session, g)
        finally:
            _svc._SERVICES = prev

        assert result.phase_after == PHASE_EXECUTING
        # 验证 session.add 被调用了 NOTES_PER_ROUND 次（run）+ 1（goal_round）
        assert session.add.call_count == NOTES_PER_ROUND + 1
        assert g.phase == PHASE_EXECUTING

    async def test_preparing_uses_round_allocator_when_available(self):
        """注入 round_allocator 时，每台 active device = 1 个 run，payload 带 preassigned_slot。"""
        from types import SimpleNamespace as NS
        from uuid import uuid4

        from matrix.agent._services import AgentServices
        from matrix.agent.protocols import ChosenSlot

        g = _make_goal(phase=PHASE_PREPARING, current_round=1)
        session = MagicMock()
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=exec_result)

        # 注入 3 个预分配 slot（不同 device，间隔 15min）
        base = datetime.now(timezone.utc)
        slots = [
            ChosenSlot(
                device_id=uuid4(),
                account_id=uuid4(),
                reason="round_allocator.match",
                scheduled_at=base + timedelta(minutes=15 * i),
                style_hint=STYLE_ROTATION[i],
            )
            for i in range(3)
        ]
        round_alloc = NS(
            count_active_devices=AsyncMock(return_value=3),
            allocate=AsyncMock(return_value=slots),
            is_slot_valid=AsyncMock(return_value=True),
        )
        services = AgentServices(
            llm=MagicMock(),
            kb_retriever=MagicMock(),
            kb_writer=MagicMock(),
            device_publisher=MagicMock(),
            device_collector=MagicMock(),
            notifier=MagicMock(),
            round_allocator=round_alloc,
        )

        from matrix.agent import _services as _svc
        prev = _svc._SERVICES
        _svc._SERVICES = services
        try:
            result = await advance_goal(session, g)
        finally:
            _svc._SERVICES = prev

        assert result.phase_after == PHASE_EXECUTING
        # 3 个 run + 1 个 goal_round
        assert session.add.call_count == 3 + 1
        # 至少有一次 session.add 收到带 preassigned_slot 的 AgentRun
        runs_with_slot = [
            call_args.args[0]
            for call_args in session.add.call_args_list
            if len(call_args.args) >= 1
            and hasattr(call_args.args[0], "payload")
            and isinstance(call_args.args[0].payload, dict)
            and "preassigned_slot" in call_args.args[0].payload
        ]
        assert len(runs_with_slot) == 3
        # 验证 style_hint 来自 STYLE_ROTATION
        hints = [
            r.payload["brief"].get("style_hint") for r in runs_with_slot
        ]
        assert hints == list(STYLE_ROTATION[:3])
        round_alloc.count_active_devices.assert_awaited_once()
        round_alloc.allocate.assert_awaited_once()

    async def test_executing_waits_when_runs_pending(self):
        g = _make_goal(phase=PHASE_EXECUTING, current_round=1)
        session = MagicMock()
        # 模拟还有 2 条 run 在 running
        result_mock = MagicMock()
        result_mock.scalar.return_value = 2
        session.execute = AsyncMock(return_value=result_mock)

        result = await advance_goal(session, g)
        # 留在 EXECUTING，phase 不变
        assert result.phase_after == PHASE_EXECUTING
        assert "still running" in result.action

    async def test_executing_to_monitoring_when_all_done(self):
        g = _make_goal(phase=PHASE_EXECUTING, current_round=1)
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar.return_value = 0  # 0 个 running
        session.execute = AsyncMock(return_value=result_mock)
        session.flush = AsyncMock()

        result = await advance_goal(session, g)
        assert result.phase_after == PHASE_MONITORING
        assert g.phase == PHASE_MONITORING

    async def test_monitoring_to_summarizing(self):
        g = _make_goal(phase=PHASE_MONITORING, current_round=1)
        session = MagicMock()
        # 模拟 _gather_round_kpi 和 _write_round_kpi 的 DB 调用
        # 1 次 execute 拉 runs，per run 1 次 execute 找 note，per note 1 次 execute 找 metrics
        runs_result = MagicMock()
        runs_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=runs_result)
        session.get = AsyncMock(return_value=None)  # goal_round 不存在
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        result = await advance_goal(session, g)
        assert result.phase_after == PHASE_SUMMARIZING
        assert g.phase == PHASE_SUMMARIZING

    async def test_summarizing_to_deciding(self):
        g = _make_goal(phase=PHASE_SUMMARIZING, current_round=1)
        session = MagicMock()
        # mock _gather_round_kpi 返回空 + _summarize_round 跑完
        runs_result = MagicMock()
        runs_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=runs_result)
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        # patch summarize_goal_to_kb 防止它真去调 embedder
        with patch(
            "matrix.agent.orchestrator.summarize_goal_to_kb",
            new=AsyncMock(),
            create=True,
        ):
            # also need to patch the import in summarize module
            with patch(
                "matrix.agent.summarize.summarize_goal_to_kb",
                AsyncMock(),
            ):
                result = await advance_goal(session, g)
        assert result.phase_after == PHASE_DECIDING
        assert g.phase == PHASE_DECIDING
        assert g.learning_summary  # 应写入

    async def test_deciding_continues_when_should_continue(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        g = _make_goal(
            phase=PHASE_DECIDING, current_round=1, max_rounds=3, deadline=future
        )
        session = MagicMock()
        runs_result = MagicMock()
        runs_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=runs_result)
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        result = await advance_goal(session, g)
        assert result.phase_after == PHASE_PREPARING
        assert g.phase == PHASE_PREPARING
        assert g.current_round == 2  # 续跑 +1

    async def test_deciding_dones_when_max_rounds_reached(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        g = _make_goal(
            phase=PHASE_DECIDING, current_round=3, max_rounds=3, deadline=future
        )
        session = MagicMock()
        runs_result = MagicMock()
        runs_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=runs_result)
        session.flush = AsyncMock()
        session.commit = AsyncMock()

        result = await advance_goal(session, g)
        assert result.phase_after == PHASE_DONE
        assert g.phase == PHASE_DONE
        assert g.status == "achieved"


# ---------------------------------------------------------------------------
# v0.7+ 第 2 期：round_number 真列 + _check_runs_done 行为
# ---------------------------------------------------------------------------


class TestCheckRunsDoneRoundColumn:
    """P0-2 回归：_check_runs_done 走 AgentRun.round_number 真列。

    mock session：模拟 count(query) 返 N。验证查询条件包含 ``round_number ==``，
    不再 cast payload JSONB。
    """

    async def test_pending_to_preparing_uses_round_number_column_filter(self):
        """_check_runs_done 的 SQL 应该按真列 round_number 过滤，不读 JSONB。"""
        from matrix.agent.orchestrator import _check_runs_done

        g = _make_goal(phase=PHASE_EXECUTING)
        session = MagicMock()
        result = MagicMock()
        result.scalar.return_value = 0
        session.execute = AsyncMock(return_value=result)

        await _check_runs_done(session, g.id, round_number=2)

        # 抓一次实际构造的 stmt
        stmt = session.execute.call_args[0][0]
        sql_text = str(stmt.compile())
        assert "agent_runs.round_number" in sql_text, (
            f"_check_runs_done SQL 应该引用 round_number 真列，实际：{sql_text}"
        )
        # 不应该再用 payload 的 JSONB cast
        assert "payload" not in sql_text.lower().replace("payload_round", ""), (
            f"_check_runs_done 仍引用 JSONB payload：{sql_text}"
        )


class TestCheckRunsDoneBehaviors:
    """P0-2 行为测试：用 mock count() 模拟"本轮是否还有跑中的 run"。"""

    async def test_returns_true_when_count_is_zero(self):
        from matrix.agent.orchestrator import _check_runs_done

        session = MagicMock()
        result = MagicMock()
        result.scalar.return_value = 0
        session.execute = AsyncMock(return_value=result)
        ok = await _check_runs_done(session, uuid.uuid4(), round_number=1)
        assert ok is True

    async def test_returns_false_when_count_is_positive(self):
        from matrix.agent.orchestrator import _check_runs_done

        session = MagicMock()
        result = MagicMock()
        result.scalar.return_value = 2
        session.execute = AsyncMock(return_value=result)
        ok = await _check_runs_done(session, uuid.uuid4(), round_number=1)
        assert ok is False
