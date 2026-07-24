"""DailyDigest 单元测试。

覆盖：
- 有通知时生成日报并写入 notifications
- 无通知时跳过不写
- LLM 失败不抛异常、不挡流程
- Worker 定时计算下次运行时间
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from matrix.agent.daily_digest import (
    DailyDigestConfig,
    DailyDigestGenerator,
    DailyDigestWorker,
)
from matrix.db.models import Notification


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclass
class _FakeCompletionResult:
    text: str
    completion_tokens: int = 42


class _FakeLLMClient:
    def __init__(self, text: str = "日报标题\n这是日报正文。") -> None:
        self._text = text

    async def complete(self, *args: Any, **kwargs: Any) -> _FakeCompletionResult:
        return _FakeCompletionResult(self._text)


def _make_session_factory(rows: list[Notification] | None = None) -> tuple[Any, MagicMock]:
    session = MagicMock()

    @asynccontextmanager
    async def factory():
        yield session

    return factory, session


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generates_digest_when_notifications_exist():
    session = MagicMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    business_row = MagicMock()
    business_row.id = "biz-1"
    business_row.name = "测试业务"

    # 模拟 businesses 查询
    business_result = MagicMock()
    business_result.all.return_value = [(business_row.id, business_row.name)]

    # 模拟 notifications 查询：返回一条通知
    note = Notification(
        code="note.published",
        severity="success",
        title="笔记已发布：hello",
        body="跟踪效果。",
        payload={"business_id": "biz-1"},
    )
    note.created_at = datetime.now(UTC)
    notif_result = MagicMock()
    notif_result.scalars.return_value.all.return_value = [note]

    def _side_effect(stmt):
        # 简单按语句类型返回：第一次是 businesses，第二次是 notifications
        # （不能看 str(stmt.froms)：notifications 表现在有 business_id → businesses 的 FK，
        #   table repr 里也会出现 "businesses" 字样，会误判）
        if "FROM businesses" in str(stmt):
            return business_result
        return notif_result

    session.execute.side_effect = _side_effect

    @asynccontextmanager
    async def factory():
        yield session

    generator = DailyDigestGenerator(
        session_factory=factory,
        llm_client=_FakeLLMClient("昨日概况\n共发布 1 条笔记，暂无异常。"),
    )
    created = await generator.run_once()

    assert created == 1
    assert session.add.call_count == 1
    written = session.add.call_args[0][0]
    assert written.code == "daily.digest"
    assert written.title == "昨日概况"
    assert "共发布 1 条笔记" in written.body


@pytest.mark.asyncio
async def test_skips_when_no_notifications():
    session = MagicMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    business_result = MagicMock()
    business_result.all.return_value = [("biz-1", "测试业务")]

    notif_result = MagicMock()
    notif_result.scalars.return_value.all.return_value = []

    session.execute.side_effect = [business_result, notif_result, notif_result]

    @asynccontextmanager
    async def factory():
        yield session

    generator = DailyDigestGenerator(
        session_factory=factory,
        llm_client=_FakeLLMClient(),
    )
    created = await generator.run_once()

    assert created == 0
    assert session.add.call_count == 0


@pytest.mark.asyncio
async def test_llm_failure_is_swallowed():
    session = MagicMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    business_result = MagicMock()
    business_result.all.return_value = [("biz-1", "测试业务")]

    note = Notification(
        code="note.published",
        severity="success",
        title="笔记已发布：hello",
        body="跟踪效果。",
        payload={"business_id": "biz-1"},
    )
    note.created_at = datetime.now(UTC)
    notif_result = MagicMock()
    notif_result.scalars.return_value.all.return_value = [note]

    session.execute.side_effect = [business_result, notif_result, notif_result]

    @asynccontextmanager
    async def factory():
        yield session

    bad_client = MagicMock()
    bad_client.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

    generator = DailyDigestGenerator(session_factory=factory, llm_client=bad_client)
    created = await generator.run_once()

    # 一个业务失败，整体返回 0；关键是不抛异常
    assert created == 0
    assert session.add.call_count == 0


def test_worker_next_run_crosses_day():
    worker = DailyDigestWorker(
        session_factory=MagicMock(),
        llm_client=MagicMock(),
        config=DailyDigestConfig(hour=9, minute=0),
    )
    now = datetime(2026, 7, 23, 10, 0, 0, tzinfo=UTC)
    nxt = worker._next_run(now)
    assert nxt == datetime(2026, 7, 24, 9, 0, 0, tzinfo=UTC)

    now2 = datetime(2026, 7, 23, 8, 0, 0, tzinfo=UTC)
    nxt2 = worker._next_run(now2)
    assert nxt2 == datetime(2026, 7, 23, 9, 0, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_worker_runs_once_and_stops():
    generator = MagicMock()
    generator.run_once = AsyncMock(return_value=2)

    worker = DailyDigestWorker(
        session_factory=MagicMock(),
        llm_client=MagicMock(),
        config=DailyDigestConfig(hour=0, minute=0),
    )
    # 直接替换 generator，避免真调 LLM
    worker._generator = generator

    # 手动触发一次
    count = await worker._generator.run_once()
    assert count == 2
    generator.run_once.assert_awaited_once()


@pytest.mark.asyncio
async def test_buckets_by_business_id_column():
    """W5：分桶优先用 notifications.business_id 新列（payload 没有也能归桶），
    且写入的日报自带 business_id 列。"""

    biz_a = uuid.uuid4()
    biz_b = uuid.uuid4()

    session = MagicMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    business_result = MagicMock()
    business_result.all.return_value = [(str(biz_a), "业务A"), (str(biz_b), "业务B")]

    # payload 不带 business_id，只有新列有
    note_a = Notification(
        code="note.published", severity="success", title="t", body="b", payload={}
    )
    note_a.business_id = biz_a
    note_a.created_at = datetime.now(UTC)
    note_b = Notification(
        code="note.published", severity="success", title="t", body="b", payload={}
    )
    note_b.business_id = biz_b
    note_b.created_at = datetime.now(UTC)

    notif_result = MagicMock()
    notif_result.scalars.return_value.all.return_value = [note_a, note_b]

    def _side_effect(stmt):
        if "FROM businesses" in str(stmt):
            return business_result
        return notif_result

    session.execute.side_effect = _side_effect

    @asynccontextmanager
    async def factory():
        yield session

    generator = DailyDigestGenerator(
        session_factory=factory,
        llm_client=_FakeLLMClient("标题\n正文"),
    )
    created = await generator.run_once()

    # 两个业务各出一条日报
    assert created == 2
    assert session.add.call_count == 2
    written_bids = {
        str(c.args[0].business_id) for c in session.add.call_args_list
    }
    assert written_bids == {str(biz_a), str(biz_b)}
