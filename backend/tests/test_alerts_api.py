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


@pytest.mark.asyncio
async def test_scan_fills_business_id(client: AsyncClient, fake_db: FakeDB) -> None:
    """W5：手动扫描写 alerts 时按设备/账号推导 business_id 回填。"""
    from datetime import UTC, datetime, timedelta

    from matrix.db.models import Account as AccountORM
    from matrix.db.models import Device as DeviceORM

    biz = uuid.uuid4()
    dev = DeviceORM(
        id=uuid.uuid4(),
        nickname="手机1",
        tags=[],
        status="active",
        last_heartbeat=datetime.now(UTC) - timedelta(seconds=600),  # 触发离线
        business_id=biz,
    )
    acct = AccountORM(
        id=uuid.uuid4(),
        handle=f"acct-{uuid.uuid4().hex[:8]}",
        status="active",
        risk_score=0.95,  # 触发风控
        business_id=biz,
    )
    fake_db.store[(DeviceORM, dev.id)] = dev
    fake_db.store[(AccountORM, acct.id)] = acct

    r = await client.post("/api/v1/alerts/scan")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    by_code = {i["code"]: i for i in items}
    assert by_code["DEVICE_OFFLINE"]["business_id"] == str(biz)
    assert by_code["RISK_BLOCKED"]["business_id"] == str(biz)
