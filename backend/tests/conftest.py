"""测试共享 fixture（v0.7+ 业务模型重构）。

提供：
- default_business：每个测试自动建一个 active 业务（slug='test-default'），测试结束清理
- business_factory：factory 风格，按需建业务
- 7 张表的 fixture 都接受 business_id 参数，缺省用 default_business

跑测试时 alembic 必须已 upgrade head（015 + 017 都跑过，businesses 表存在）。
"""
from __future__ import annotations

import uuid
from typing import AsyncIterator, Optional

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from matrix.db.models import (
    Account as AccountORM,
    Business as BusinessORM,
    Device as DeviceORM,
    Goal as GoalORM,
    KbDocument as KbDocumentORM,
    Note as NoteORM,
    Persona as PersonaORM,
)


def _get_test_url() -> str:
    import os

    return os.environ.get("DATABASE_URL", "postgresql+asyncpg://matrix:matrix_dev@postgres:5432/matrix")


@pytest_asyncio.fixture
async def engine():
    """每个测试一个 engine（用 docker 环境的 DATABASE_URL）。"""
    eng = create_async_engine(_get_test_url())
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    """每个测试一个独立 session（事务结束后 rollback）。"""
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
        # 事务自动 close，pytest-asyncio 不自动 rollback
        # 测试创建的 fixture 在事务中可见，但提交由测试或依赖函数决定
        await s.rollback()


@pytest_asyncio.fixture
async def default_business(session: AsyncSession) -> BusinessORM:
    """每个测试一个 active 业务（slug='test-default-{uuid8}'，避免互相干扰）。"""
    biz = BusinessORM(
        name="测试业务",
        slug=f"test-default-{uuid.uuid4().hex[:8]}",
        status="active",
    )
    session.add(biz)
    await session.flush()
    return biz


@pytest_asyncio.fixture
async def business_factory(session: AsyncSession):
    """工厂函数：按需建业务。"""

    async def _make(
        name: str = "测试业务",
        slug: Optional[str] = None,
        status: str = "active",
    ) -> BusinessORM:
        biz = BusinessORM(
            name=name,
            slug=slug or f"test-{uuid.uuid4().hex[:8]}",
            status=status,
        )
        session.add(biz)
        await session.flush()
        return biz

    return _make


# ---------------------------------------------------------------------------
# 7 张表 fixture（每个都接受 business_id，缺省用 default_business）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def account_fixture(session: AsyncSession, default_business: BusinessORM):
    async def _make(
        handle: str = "@test",
        business_id: Optional[uuid.UUID] = None,
    ) -> AccountORM:
        # 简化：不强求 persona/device FK（测试场景按需补）
        from matrix.db.models import Device, Persona

        dev = DeviceORM(
            nickname=f"dev-{uuid.uuid4().hex[:6]}",
            business_id=business_id or default_business.id,
            status="pending",
        )
        session.add(dev)
        await session.flush()
        per = PersonaORM(
            name=f"per-{uuid.uuid4().hex[:6]}",
            tone="tone",
            style_guide="sg",
            business_id=business_id or default_business.id,
        )
        session.add(per)
        await session.flush()
        acct = AccountORM(
            handle=handle,
            device_id=dev.id,
            persona_id=per.id,
            business_id=business_id or default_business.id,
            status="pending",
            risk_score=0,
        )
        session.add(acct)
        await session.flush()
        return acct

    return _make


@pytest_asyncio.fixture
async def device_fixture(session: AsyncSession, default_business: BusinessORM):
    async def _make(
        nickname: str = "test-device",
        business_id: Optional[uuid.UUID] = None,
    ) -> DeviceORM:
        d = DeviceORM(
            nickname=nickname,
            business_id=business_id or default_business.id,
            status="pending",
        )
        session.add(d)
        await session.flush()
        return d

    return _make


@pytest_asyncio.fixture
async def persona_fixture(session: AsyncSession, default_business: BusinessORM):
    async def _make(
        name: str = "test-persona",
        business_id: Optional[uuid.UUID] = None,
    ) -> PersonaORM:
        p = PersonaORM(
            name=name,
            tone="tone",
            style_guide="sg",
            business_id=business_id or default_business.id,
        )
        session.add(p)
        await session.flush()
        return p

    return _make


@pytest_asyncio.fixture
async def goal_fixture(session: AsyncSession, default_business: BusinessORM):
    async def _make(
        type: str = "publish_note",
        target: Optional[dict] = None,
        business_id: Optional[uuid.UUID] = None,
    ) -> GoalORM:
        g = GoalORM(
            type=type,
            target=target or {"theme": "测试主题"},
            business_id=business_id or default_business.id,
            status="active",
        )
        session.add(g)
        await session.flush()
        return g

    return _make


@pytest_asyncio.fixture
async def note_fixture(session: AsyncSession, default_business: BusinessORM):
    async def _make(
        title: str = "测试笔记",
        business_id: Optional[uuid.UUID] = None,
    ) -> NoteORM:
        n = NoteORM(
            title=title,
            content="content",
            business_id=business_id or default_business.id,
        )
        session.add(n)
        await session.flush()
        return n

    return _make


@pytest_asyncio.fixture
async def kb_document_fixture(session: AsyncSession, default_business: BusinessORM):
    async def _make(
        type: str = "strategy_card",
        content: str = "kb content",
        business_id: Optional[uuid.UUID] = None,
    ) -> KbDocumentORM:
        d = KbDocumentORM(
            type=type,
            content=content,
            business_id=business_id or default_business.id,
        )
        session.add(d)
        await session.flush()
        return d

    return _make