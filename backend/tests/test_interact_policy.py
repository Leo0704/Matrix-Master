"""matrix.agent.interact_policy 单元测试。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from matrix.agent.interact_policy import (
    DISABLED_STATUSES,
    RISK_COMMENT_BLOCKED,
    RISK_SKIP_ALL,
    InteractPolicy,
    PolicyDecision,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_account(
    *,
    status: str = "active",
    risk_score: float = 0.1,
    account_id: UUID | None = None,
) -> MagicMock:
    a = MagicMock()
    a.id = account_id or uuid4()
    a.handle = "u1"
    a.status = status
    a.risk_score = risk_score
    a.deleted_at = None
    return a


def _make_note(*, platform_note_id: str, note_id: UUID | None = None) -> MagicMock:
    n = MagicMock()
    n.id = note_id or uuid4()
    n.platform_note_id = platform_note_id
    n.deleted_at = None
    return n


class _MockSessionCtx:
    """模拟 AsyncSession 上下文管理器：每个 attribute 都能被链式调用。"""

    def __init__(self, session: AsyncMock) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncMock:
        return self._session

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _make_session_factory(
    *,
    account: Any = None,
    note: Any = None,
    interaction_exists: bool = False,
) -> Any:
    """构造一个 fake session factory，每个 query 返回预先设定的值。

    支持的 query 顺序（按实际 SQL 调用顺序）：
    1) Note.platform_note_id 查询 → return [note] 或 []
    2) Interaction 查询 → return [interaction] 或 []
    Account 走 session.get(Account, id) → return account 或 None
    """
    session = AsyncMock()
    # 默认 session.get(Account, ...) 返回传入的 account
    session.get = AsyncMock(return_value=account)
    # 默认 session.execute 顺序由调用方决定；我们用 side_effect 队列
    # 注意：Note 查询 → scalars().first()；Interaction 查询 → scalars().first()
    # 第一次 execute() 是 Note 查询；第二次是 Interaction 查询

    # 用一个可消费的 list 模拟"先 Note 再 Interaction"
    scalars_returns = [
        MagicMock(),  # Note 查询的 scalars()
        MagicMock(),  # Interaction 查询的 scalars()
    ]
    scalars_returns[0].first = MagicMock(return_value=note)
    scalars_returns[1].first = MagicMock(return_value=uuid4() if interaction_exists else None)

    execute_results = [MagicMock(), MagicMock()]
    execute_results[0].scalars = MagicMock(return_value=scalars_returns[0])
    execute_results[1].scalars = MagicMock(return_value=scalars_returns[1])

    session.execute = AsyncMock(side_effect=execute_results)

    @asynccontextmanager
    async def factory():
        yield session

    return factory, session


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestInteractPolicyDedup:
    @pytest.mark.asyncio
    async def test_no_session_factory_means_no_skip(self):
        p = InteractPolicy(session_factory=None)
        d = await p.should_skip(
            account_id=uuid4(), target_note_id="x", kind="like"
        )
        assert d.skip is False

    @pytest.mark.asyncio
    async def test_already_liked_skipped(self):
        account_id = uuid4()
        note_id = uuid4()
        account = _make_account(account_id=account_id)
        note = _make_note(platform_note_id="xhs-1", note_id=note_id)
        factory, _ = _make_session_factory(
            account=account, note=note, interaction_exists=True
        )
        p = InteractPolicy(session_factory=factory)
        d = await p.should_skip(
            account_id=account_id, target_note_id="xhs-1", kind="like"
        )
        assert d.skip is True
        assert d.reason == "DEDUPED"
        assert "already like" in d.message

    @pytest.mark.asyncio
    async def test_first_time_not_deduped(self):
        account_id = uuid4()
        note_id = uuid4()
        account = _make_account(account_id=account_id)
        note = _make_note(platform_note_id="xhs-1", note_id=note_id)
        factory, _ = _make_session_factory(
            account=account, note=note, interaction_exists=False
        )
        p = InteractPolicy(session_factory=factory)
        d = await p.should_skip(
            account_id=account_id, target_note_id="xhs-1", kind="like"
        )
        assert d.skip is False

    @pytest.mark.asyncio
    async def test_unknown_target_note_id_not_deduped(self):
        """平台 id 没索引到本地 Note → 当作新笔记，不去重。"""
        account_id = uuid4()
        account = _make_account(account_id=account_id)
        factory, _ = _make_session_factory(
            account=account, note=None, interaction_exists=False
        )
        p = InteractPolicy(session_factory=factory)
        d = await p.should_skip(
            account_id=account_id, target_note_id="never-seen", kind="like"
        )
        assert d.skip is False


class TestInteractPolicyAdaptive:
    @pytest.mark.asyncio
    async def test_banned_account_skipped(self):
        account = _make_account(status="banned")
        factory, _ = _make_session_factory(account=account)
        p = InteractPolicy(session_factory=factory)
        d = await p.should_skip(
            account_id=uuid4(), target_note_id="x", kind="like"
        )
        assert d.skip is True
        assert d.reason == "ACCOUNT_DISABLED"

    @pytest.mark.asyncio
    async def test_suspended_account_skipped(self):
        for s in ("suspended", "disabled"):
            account = _make_account(status=s)
            factory, _ = _make_session_factory(account=account)
            p = InteractPolicy(session_factory=factory)
            d = await p.should_skip(
                account_id=uuid4(), target_note_id="x", kind="like"
            )
            assert d.skip is True
            assert d.reason == "ACCOUNT_DISABLED", s

    @pytest.mark.asyncio
    async def test_risk_too_high_skips_all(self):
        account = _make_account(risk_score=0.9)
        factory, _ = _make_session_factory(account=account)
        p = InteractPolicy(session_factory=factory)
        for kind in ("like", "comment"):
            d = await p.should_skip(
                account_id=uuid4(), target_note_id="x", kind=kind
            )
            assert d.skip is True
            assert d.reason == "RISK_TOO_HIGH", kind

    @pytest.mark.asyncio
    async def test_risk_mid_blocks_comment_only(self):
        """0.7 <= risk < 0.85：like 仍允许，comment 阻止。"""
        account = _make_account(risk_score=0.75)
        factory, _ = _make_session_factory(account=account, note=None)
        p = InteractPolicy(session_factory=factory)
        # like 仍 OK
        d_like = await p.should_skip(
            account_id=uuid4(), target_note_id="x", kind="like"
        )
        assert d_like.skip is False
        # comment 阻止
        d_comment = await p.should_skip(
            account_id=uuid4(), target_note_id="x", kind="comment"
        )
        assert d_comment.skip is True
        assert d_comment.reason == "RISK_COMMENT_BLOCKED"

    @pytest.mark.asyncio
    async def test_low_risk_continues_normally(self):
        account = _make_account(risk_score=0.1)
        factory, _ = _make_session_factory(account=account, note=None)
        p = InteractPolicy(session_factory=factory)
        for kind in ("like", "comment"):
            d = await p.should_skip(
                account_id=uuid4(), target_note_id="x", kind=kind
            )
            assert d.skip is False, kind

    @pytest.mark.asyncio
    async def test_account_not_found_skipped(self):
        factory, _ = _make_session_factory(account=None)
        p = InteractPolicy(session_factory=factory)
        d = await p.should_skip(
            account_id=uuid4(), target_note_id="x", kind="like"
        )
        assert d.skip is True
        assert d.reason == "ACCOUNT_NOT_FOUND"


class TestInteractPolicyRobustness:
    @pytest.mark.asyncio
    async def test_db_exception_does_not_break_flow(self):
        @asynccontextmanager
        async def broken_factory():
            raise RuntimeError("db down")
            yield  # unreachable, just for typing

        p = InteractPolicy(session_factory=broken_factory)
        d = await p.should_skip(
            account_id=uuid4(), target_note_id="x", kind="like"
        )
        # 兜底：DB 挂掉时不 skip，让原流程跑（人肉决定要不要 retry）
        assert d.skip is False

    def test_constants_exposed(self):
        assert RISK_COMMENT_BLOCKED == 0.7
        assert RISK_SKIP_ALL == 0.85
        assert "banned" in DISABLED_STATUSES
        assert "suspended" in DISABLED_STATUSES
        assert "disabled" in DISABLED_STATUSES
