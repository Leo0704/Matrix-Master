"""通知 API 集成测试（FakeAsyncSession，不连真实 DB）。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from matrix.api import deps
from matrix.api.app import create_app
from matrix.api.tests.test_api import FakeAsyncSession, FakeDB
from matrix.db.models import Notification as NotificationORM


@pytest_asyncio.fixture
async def fake_db() -> FakeDB:
    return FakeDB()


@pytest_asyncio.fixture
async def app(fake_db: FakeDB):
    application = create_app(
        database_url="sqlite+aiosqlite:///:memory:",
        enable_monitoring_middleware=False,
    )

    async def override_get_db() -> AsyncIterator[FakeAsyncSession]:
        sess = FakeAsyncSession(fake_db)
        try:
            yield sess
            await sess.commit()
        except Exception:
            await sess.rollback()
            raise
        finally:
            await sess.close()

    application.dependency_overrides[deps.get_db] = override_get_db
    yield application
    application.dependency_overrides.pop(deps.get_db, None)


@pytest_asyncio.fixture
async def client(app) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.mark.asyncio
async def test_delete_notification(client: AsyncClient, fake_db: FakeDB) -> None:
    note = NotificationORM(
        id=uuid.uuid4(),
        recipient="operator",
        code="note.published",
        severity="success",
        title="测试",
        body="测试",
        payload={},
    )
    fake_db.store[(NotificationORM, note.id)] = note

    r = await client.delete(f"/api/v1/notifications/{note.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert (NotificationORM, note.id) not in fake_db.store


@pytest.mark.asyncio
async def test_clear_read_notifications(client: AsyncClient, fake_db: FakeDB) -> None:
    read_id = uuid.uuid4()
    unread_id = uuid.uuid4()
    fake_db.store[(NotificationORM, read_id)] = NotificationORM(
        id=read_id,
        recipient="operator",
        code="note.published",
        severity="success",
        title="已读",
        body="已读",
        read_at=datetime.now(UTC),
        payload={},
    )
    fake_db.store[(NotificationORM, unread_id)] = NotificationORM(
        id=unread_id,
        recipient="operator",
        code="note.published",
        severity="success",
        title="未读",
        body="未读",
        payload={},
    )

    r = await client.post("/api/v1/notifications/clear-read")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert (NotificationORM, read_id) not in fake_db.store
    assert (NotificationORM, unread_id) in fake_db.store


# ---------------------------------------------------------------------------
# W5：business_id 业务约束
# ---------------------------------------------------------------------------


def test_business_scope_sql_prefers_column_with_fk_fallback() -> None:
    """_business_scope：优先新列 = X；老数据（列 IS NULL）回退 goal/note/device EXISTS。"""
    from matrix.api.routes.notifications import _business_scope

    sql = str(_business_scope(uuid.uuid4()))
    assert "notifications.business_id" in sql
    assert "IS NULL" in sql
    assert "EXISTS" in sql
    # 三类 FK 回退都在
    assert "goals" in sql
    assert "notes" in sql
    assert "devices" in sql


@pytest.mark.asyncio
async def test_list_notifications_accepts_business_id(client: AsyncClient, fake_db: FakeDB) -> None:
    n = NotificationORM(
        id=uuid.uuid4(),
        recipient="operator",
        code="note.published",
        severity="success",
        title="测试",
        body="测试",
        payload={},
        created_at=datetime.now(UTC),
    )
    fake_db.store[(NotificationORM, n.id)] = n

    r = await client.get(f"/api/v1/notifications?business_id={uuid.uuid4()}")
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body


@pytest.mark.asyncio
async def test_mark_read_with_business_id(client: AsyncClient, fake_db: FakeDB) -> None:
    n = NotificationORM(
        id=uuid.uuid4(),
        recipient="operator",
        code="note.published",
        severity="success",
        title="未读",
        body="未读",
        payload={},
    )
    fake_db.store[(NotificationORM, n.id)] = n

    r = await client.post(
        "/api/v1/notifications/read",
        json={"business_id": str(uuid.uuid4())},
    )
    assert r.status_code == 200
    assert r.json()["marked"] >= 0


@pytest.mark.asyncio
async def test_clear_read_with_business_id(client: AsyncClient, fake_db: FakeDB) -> None:
    read_id = uuid.uuid4()
    fake_db.store[(NotificationORM, read_id)] = NotificationORM(
        id=read_id,
        recipient="operator",
        code="note.published",
        severity="success",
        title="已读",
        body="已读",
        read_at=datetime.now(UTC),
        payload={},
    )

    r = await client.post(
        f"/api/v1/notifications/clear-read?business_id={uuid.uuid4()}"
    )
    assert r.status_code == 200
    assert "deleted" in r.json()


@pytest.mark.asyncio
async def test_delete_with_business_id(client: AsyncClient, fake_db: FakeDB) -> None:
    n = NotificationORM(
        id=uuid.uuid4(),
        recipient="operator",
        code="note.published",
        severity="success",
        title="测试",
        body="测试",
        payload={},
    )
    fake_db.store[(NotificationORM, n.id)] = n

    r = await client.delete(
        f"/api/v1/notifications/{n.id}?business_id={uuid.uuid4()}"
    )
    assert r.status_code == 200
    assert "deleted" in r.json()
