"""告警 API 集成测试（FakeAsyncSession）。"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from matrix.api import deps
from matrix.api.app import create_app
from matrix.api.tests.test_api import FakeAsyncSession, FakeDB
from matrix.db.models import Alert as AlertORM


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
async def test_delete_alert(client: AsyncClient, fake_db: FakeDB) -> None:
    alert = AlertORM(
        id=uuid.uuid4(),
        code="DEVICE_OFFLINE",
        severity="critical",
        message="设备离线",
        resolved=False,
    )
    fake_db.store[(AlertORM, alert.id)] = alert

    r = await client.delete(f"/api/v1/alerts/{alert.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert (AlertORM, alert.id) not in fake_db.store


@pytest.mark.asyncio
async def test_clear_resolved_alerts(client: AsyncClient, fake_db: FakeDB) -> None:
    resolved_id = uuid.uuid4()
    unresolved_id = uuid.uuid4()
    fake_db.store[(AlertORM, resolved_id)] = AlertORM(
        id=resolved_id,
        code="DEVICE_OFFLINE",
        severity="critical",
        message="设备离线",
        resolved=True,
    )
    fake_db.store[(AlertORM, unresolved_id)] = AlertORM(
        id=unresolved_id,
        code="RISK_BLOCKED",
        severity="critical",
        message="账号风控",
        resolved=False,
    )

    r = await client.post("/api/v1/alerts/clear-resolved")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert (AlertORM, resolved_id) not in fake_db.store
    assert (AlertORM, unresolved_id) in fake_db.store
