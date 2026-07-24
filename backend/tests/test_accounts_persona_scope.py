"""W5：accounts PATCH 换绑 persona 必须同业务（persona business_id=NULL 视为全局共享）。"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from matrix.api import deps
from matrix.api.app import create_app
from matrix.api.tests.test_api import FakeAsyncSession, FakeDB
from matrix.db.models import Account as AccountORM
from matrix.db.models import KbDocument as KbDocumentORM


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


_BIZ_A = uuid.uuid4()
_BIZ_B = uuid.uuid4()


def _seed_account(fake_db: FakeDB) -> AccountORM:
    a = AccountORM(
        id=uuid.uuid4(),
        handle=f"acct-{uuid.uuid4().hex[:8]}",
        status="active",
        risk_score=0,
        business_id=_BIZ_A,
    )
    fake_db.store[(AccountORM, a.id)] = a
    return a


def _seed_persona(fake_db: FakeDB, business_id: uuid.UUID | None) -> KbDocumentORM:
    doc = KbDocumentORM(
        id=uuid.uuid4(),
        type="persona",
        title="人设",
        content="tone: 亲切",
        metadata_={},
        version=1,
        is_published=True,
        business_id=business_id,
    )
    fake_db.store[(KbDocumentORM, doc.id)] = doc
    return doc


@pytest.mark.asyncio
async def test_rebind_persona_cross_business_rejected(
    client: AsyncClient, fake_db: FakeDB
) -> None:
    a = _seed_account(fake_db)
    persona = _seed_persona(fake_db, business_id=_BIZ_B)
    r = await client.patch(
        f"/api/v1/accounts/{a.id}", json={"persona_id": str(persona.id)}
    )
    assert r.status_code == 409
    assert a.persona_id is None


@pytest.mark.asyncio
async def test_rebind_persona_same_business_allowed(
    client: AsyncClient, fake_db: FakeDB
) -> None:
    a = _seed_account(fake_db)
    persona = _seed_persona(fake_db, business_id=_BIZ_A)
    r = await client.patch(
        f"/api/v1/accounts/{a.id}", json={"persona_id": str(persona.id)}
    )
    assert r.status_code == 200
    assert r.json()["persona_id"] == str(persona.id)


@pytest.mark.asyncio
async def test_rebind_persona_global_shared_allowed(
    client: AsyncClient, fake_db: FakeDB
) -> None:
    """persona business_id=NULL（全局共享，兼容存量）→ 允许绑定。"""
    a = _seed_account(fake_db)
    persona = _seed_persona(fake_db, business_id=None)
    r = await client.patch(
        f"/api/v1/accounts/{a.id}", json={"persona_id": str(persona.id)}
    )
    assert r.status_code == 200
    assert r.json()["persona_id"] == str(persona.id)
