"""W5：notifications 业务过滤真实 DB 集成测试。

验证 ``_business_scope`` 的语义：
- 新列 business_id == X 的行命中（优先）
- 新列为 NULL 的老数据回退 goal/note/device FK EXISTS 推导
- 其他业务的行不命中
- 写操作（mark read）带 scope 只动本业务的行
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from matrix.api.routes.notifications import _business_scope
from matrix.db.models import Goal as GoalORM
from matrix.db.models import Notification as NotificationORM


def _mk_notification(**overrides) -> NotificationORM:
    n = NotificationORM(
        id=uuid.uuid4(),
        recipient="operator",
        code="goal.round.prepared",
        severity="info",
        title="t",
        body="b",
        payload={},
    )
    for k, v in overrides.items():
        setattr(n, k, v)
    return n


@pytest.mark.asyncio
async def test_business_scope_column_preferred_and_fk_fallback(
    session, business_factory
):
    biz_a = await business_factory(name="A", slug=f"biz-a-{uuid.uuid4().hex[:6]}")
    biz_b = await business_factory(name="B", slug=f"biz-b-{uuid.uuid4().hex[:6]}")

    goal_a = GoalORM(type="publish_note", target={"theme": "a"}, business_id=biz_a.id)
    goal_b = GoalORM(type="publish_note", target={"theme": "b"}, business_id=biz_b.id)
    session.add_all([goal_a, goal_b])
    await session.flush()

    # 1) 新列直接命中 biz_a
    n_col_a = _mk_notification(business_id=biz_a.id)
    # 2) 老数据（列 NULL）经 goal FK 回退命中 biz_a
    n_fk_a = _mk_notification(goal_id=goal_a.id)
    # 3) biz_b 的行（列 NULL + goal_b）不命中
    n_b = _mk_notification(goal_id=goal_b.id)
    # 4) 完全没有业务信息的行不命中
    n_orphan = _mk_notification()
    session.add_all([n_col_a, n_fk_a, n_b, n_orphan])
    await session.flush()

    rows = (
        await session.execute(
            select(NotificationORM.id).where(_business_scope(biz_a.id))
        )
    ).scalars().all()
    matched = set(rows)
    assert n_col_a.id in matched
    assert n_fk_a.id in matched
    assert n_b.id not in matched
    assert n_orphan.id not in matched


@pytest.mark.asyncio
async def test_mark_read_scoped_to_business(session, business_factory):
    biz_a = await business_factory(name="A", slug=f"biz-a-{uuid.uuid4().hex[:6]}")
    biz_b = await business_factory(name="B", slug=f"biz-b-{uuid.uuid4().hex[:6]}")

    n_a = _mk_notification(business_id=biz_a.id)
    n_b = _mk_notification(business_id=biz_b.id)
    session.add_all([n_a, n_b])
    await session.flush()

    now = datetime.now(UTC)
    await session.execute(
        update(NotificationORM)
        .where(NotificationORM.read_at.is_(None))
        .where(_business_scope(biz_a.id))
        .values(read_at=now)
    )
    await session.flush()
    # ORM update 不回写内存实例，refresh 再断言
    await session.refresh(n_a)
    await session.refresh(n_b)

    assert n_a.read_at is not None
    assert n_b.read_at is None
