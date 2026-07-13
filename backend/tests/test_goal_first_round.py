"""首轮"恰好 N 条 AgentRun"测试。

P0-1 回归：``create_goal`` 删掉"启动种子"后，首轮跑出 ``notes_per_round`` 条
AgentRun（不再 +1），全部带 ``round_number=1``，没有 ``round_number IS NULL`` 的孤儿。

策略：纯 mock（匹配现有 ``test_orchestrator.py`` 风格，避开 Postgres 真表；
"1+N" 是数量关系，验证 ``session.add`` 调用次数足够说明问题；如果哪天真接
Postgres，再补一个 ``TestGoalFirstRoundE2E`` 用真表计数即可）。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from matrix.agent.orchestrator import (
    NOTES_PER_ROUND,
    PHASE_EXECUTING,
    PHASE_PREPARING,
    advance_goal,
)
from matrix.db.models import AgentRun


def _make_goal(*, phase: str, notes_per_round: int = NOTES_PER_ROUND):
    g = SimpleNamespace()
    g.id = uuid.uuid4()
    g.phase = phase
    g.current_round = 1
    g.max_rounds = 3
    g.deadline = None
    g.status = "active"
    g.target = {"theme": "测试主题"}
    g.type = "publish_note"
    g.learning_summary = None
    g.target_likes = 500
    g.notes_per_round = notes_per_round
    return g


def _mock_session():
    """Bare-minimum session stub：``session.add`` 接收；``session.execute`` 返回空。"""
    s = MagicMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    s.commit = AsyncMock()
    s.get = AsyncMock(return_value=None)
    empty = MagicMock()
    empty.scalars.return_value.all.return_value = []
    empty.scalar.return_value = 0
    s.execute = AsyncMock(return_value=empty)
    return s


class TestFirstRoundExactNRuns:
    """PENDING → PREPARING → EXECUTING：必须产生恰好 N 条带 round_number=1 的 AgentRun。"""

    async def test_exactly_N_runs_no_bootstrap(self):
        # 模拟"create_goal 已不再塞 bootstrap"的场景
        g = _make_goal(phase="PENDING")
        session = _mock_session()

        from matrix.agent import _services as _svc

        prev = _svc._SERVICES
        _svc._SERVICES = None  # 让 _prepare_round 走降级路径
        try:
            r1 = await advance_goal(session, g)
            assert r1.phase_after == PHASE_PREPARING
            # 第一次推进（仅 set_phase + commit），不该新增 AgentRun
            agent_runs_after_first = [
                c.args[0]
                for c in session.add.call_args_list
                if isinstance(c.args[0], AgentRun)
            ]
            assert agent_runs_after_first == [], (
                f"PENDING 阶段不该创建 AgentRun，但加了 {agent_runs_after_first}"
            )

            r2 = await advance_goal(session, g)
            assert r2.phase_after == PHASE_EXECUTING
        finally:
            _svc._SERVICES = prev

        # 收集两轮 add 的 AgentRun
        all_runs = [
            c.args[0]
            for c in session.add.call_args_list
            if isinstance(c.args[0], AgentRun)
        ]
        assert len(all_runs) == NOTES_PER_ROUND, (
            f"预期 {NOTES_PER_ROUND} 条 AgentRun（首轮 fan-out），"
            f"实际 {len(all_runs)}；之前 1+N bug 会让该数为 {NOTES_PER_ROUND + 1}"
        )
        # 每条都有 round_number=1
        assert all(r.round_number == 1 for r in all_runs), (
            f"全部 run 应有 round_number=1，但有 None/其他："
            f"{[(r.round_number) for r in all_runs]}"
        )
        # 同时也只有 1 条 GoalRound（PREPARING 阶段末尾写）
        from matrix.db.models import GoalRound

        goal_rounds = [
            c.args[0]
            for c in session.add.call_args_list
            if isinstance(c.args[0], GoalRound)
        ]
        assert len(goal_rounds) == 1

    async def test_pending_to_executing_recovers_round_number(self):
        """不管 create_goal 是否还塞任何 row，executor 看到的 row 都带 round_number。
        防 create_goal 未来又被改回去时走 N+1 路径。
        """
        g = _make_goal(phase="PENDING", notes_per_round=4)
        session = _mock_session()

        from matrix.agent import _services as _svc

        prev = _svc._SERVICES
        _svc._SERVICES = None
        try:
            await advance_goal(session, g)
            await advance_goal(session, g)
        finally:
            _svc._SERVICES = prev

        all_runs = [
            c.args[0]
            for c in session.add.call_args_list
            if isinstance(c.args[0], AgentRun)
        ]
        assert len(all_runs) == 4
        for r in all_runs:
            assert r.round_number == 1
            # 第 1 轮的 fallback payload 仍然带 round_number（兼容 + 索引）
            assert r.payload["round_number"] == 1
