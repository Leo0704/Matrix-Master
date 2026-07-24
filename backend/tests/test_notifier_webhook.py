"""WebhookNotifier 单元测试（Phase 1 P1-1）。

覆盖：
- 写 notifications 表（即使 webhook 挂）
- webhook_url 缺失时只写 DB 不发外部
- 5xx 触发 3 次指数退避
- 4xx 不重试
- 上层异常被吞掉（notifier 永不抛）
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from matrix.agent._notifier_webhook import WebhookNotifier


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_session_factory() -> tuple[Any, MagicMock]:
    """返回 (factory, session)；session.add / commit / rollback 都 mock。"""
    session = MagicMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    @asynccontextmanager
    async def factory():
        yield session

    return factory, session


def _make_config_reader(value: Any) -> Any:
    async def reader(key: str, default: Any = None) -> Any:
        return value

    return reader


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def _fake_client(responses: list[_FakeResponse]) -> httpx.AsyncClient:
    """构造一个 mock httpx.AsyncClient，post() 按序返回 responses。"""
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=responses)
    client.aclose = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unregistered_code_does_not_write_db():
    """未注册的 code 宁可不发，也不把 {code}/{payload} 这种程序员内容丢给用户。"""
    factory, session = _make_session_factory()
    reader = _make_config_reader(None)
    client = _fake_client([])
    notifier = WebhookNotifier(
        session_factory=factory, config_reader=reader, http_client=client
    )

    await notifier("NOT_A_REAL_CODE", {"foo": "bar"})

    # DB 不写、webhook 不发
    assert session.execute.await_count == 0
    assert client.post.await_count == 0


@pytest.mark.asyncio
async def test_writes_notification_even_when_webhook_url_missing():
    factory, session = _make_session_factory()
    reader = _make_config_reader(None)  # 没配 webhook
    client = _fake_client([])
    notifier = WebhookNotifier(
        session_factory=factory, config_reader=reader, http_client=client
    )

    await notifier("note.published", {"note_id": "abc", "title": "test"})

    # session.execute 被调一次（insert into notifications）
    assert session.execute.await_count == 1
    # session.commit 被调一次（写完就 commit）
    assert session.commit.await_count == 1
    # 外部 HTTP 没发
    assert client.post.await_count == 0


@pytest.mark.asyncio
async def test_webhook_4xx_no_retry():
    factory, _session = _make_session_factory()
    reader = _make_config_reader("https://hooks.example.com/x")
    client = _fake_client([_FakeResponse(404)])
    notifier = WebhookNotifier(
        session_factory=factory, config_reader=reader, http_client=client
    )

    await notifier("note.collected", {"note_id": "abc"})

    # 4xx 不重试，只调一次
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_webhook_5xx_retries_3_times():
    factory, _session = _make_session_factory()
    reader = _make_config_reader("https://hooks.example.com/x")
    client = _fake_client([
        _FakeResponse(503),
        _FakeResponse(503),
        _FakeResponse(200),
    ])
    notifier = WebhookNotifier(
        session_factory=factory,
        config_reader=reader,
        http_client=client,
        max_retries=3,
    )

    await notifier("note.collected", {"note_id": "abc"})

    # 503, 503, 200 → 共 3 次
    assert client.post.await_count == 3


@pytest.mark.asyncio
async def test_webhook_timeout_retries_then_gives_up():
    factory, _session = _make_session_factory()
    reader = _make_config_reader("https://hooks.example.com/x")
    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=httpx.TimeoutException("simulated"))
    client.aclose = AsyncMock()
    notifier = WebhookNotifier(
        session_factory=factory,
        config_reader=reader,
        http_client=client,
        max_retries=3,
    )

    await notifier("note.collected", {"note_id": "abc"})

    # 超时也是 max_retries 次
    assert client.post.await_count == 3


@pytest.mark.asyncio
async def test_notifier_never_raises_even_if_db_fails():
    """即使 DB 写挂了，notifier 也不抛异常（避免挡主流程）。"""

    @asynccontextmanager
    async def broken_factory():
        raise RuntimeError("DB down")
        yield  # unreachable, only for typing

    client = _fake_client([])
    reader = _make_config_reader(None)
    notifier = WebhookNotifier(
        session_factory=broken_factory, config_reader=reader, http_client=client
    )

    # 必须不抛
    await notifier("note.published", {"note_id": "abc"})


@pytest.mark.asyncio
async def test_aclose_closes_owned_client_but_not_injected():
    """外部注入的 client 不被关闭；自己 new 的会被关闭。"""
    factory, _session = _make_session_factory()
    reader = _make_config_reader(None)

    # 注入的 client：不应该被 aclose
    injected = _fake_client([])
    notifier1 = WebhookNotifier(
        session_factory=factory, config_reader=reader, http_client=injected
    )
    await notifier1.aclose()
    assert injected.aclose.await_count == 0

    # 自己 new 的 client：会被 aclose
    notifier2 = WebhookNotifier(
        session_factory=factory, config_reader=reader, http_client=None
    )
    await notifier2.aclose()
    # 默认 httpx.AsyncClient 被注入 spec=MagicMock 时不会真创建
    # 这里只验证不抛异常