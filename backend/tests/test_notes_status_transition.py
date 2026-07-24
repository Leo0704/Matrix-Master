"""W5：notes PATCH 状态流转校验 — 手动修改不允许置为 published（防造假）。

规则：PATCH status=published 仅当笔记已有 platform_note_id（确由设备发布链路发出）
才允许；其他状态流转保持原有自由编辑语义。
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from matrix.api import deps
from matrix.api.app import create_app
from matrix.api.tests.test_api import FakeAsyncSession, FakeDB
from matrix.db.models import Note as NoteORM


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


def _seed_note(fake_db: FakeDB, **overrides) -> NoteORM:
    n = NoteORM(
        id=uuid.uuid4(),
        account_id=None,
        title="草稿",
        content="正文",
        images=[],
        tags=[],
        status="draft",
        business_id=uuid.uuid4(),
    )
    for k, v in overrides.items():
        setattr(n, k, v)
    fake_db.store[(NoteORM, n.id)] = n
    return n


@pytest.mark.asyncio
async def test_patch_to_published_without_platform_note_id_rejected(
    client: AsyncClient, fake_db: FakeDB
) -> None:
    n = _seed_note(fake_db)
    r = await client.patch(f"/api/v1/notes/{n.id}", json={"status": "published"})
    assert r.status_code == 409
    # 状态没被改
    assert n.status == "draft"


@pytest.mark.asyncio
async def test_patch_to_published_with_platform_note_id_allowed(
    client: AsyncClient, fake_db: FakeDB
) -> None:
    n = _seed_note(fake_db, platform_note_id="xhs_12345")
    r = await client.patch(f"/api/v1/notes/{n.id}", json={"status": "published"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "published"
    assert body["published_at"] is not None


@pytest.mark.asyncio
async def test_patch_other_status_transitions_kept(
    client: AsyncClient, fake_db: FakeDB
) -> None:
    n = _seed_note(fake_db)
    r = await client.patch(f"/api/v1/notes/{n.id}", json={"status": "scheduled"})
    assert r.status_code == 200
    assert r.json()["status"] == "scheduled"


@pytest.mark.asyncio
async def test_patch_content_without_status_kept(
    client: AsyncClient, fake_db: FakeDB
) -> None:
    n = _seed_note(fake_db)
    r = await client.patch(f"/api/v1/notes/{n.id}", json={"content": "改后的正文"})
    assert r.status_code == 200
    assert r.json()["content"] == "改后的正文"
    assert r.json()["status"] == "draft"
