"""业务内单一 active goal 限制测试（v0.7+）。

规则：一个业务同时只能有一个 status='active' 且 phase != 'DONE' 的目标。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from matrix.api.routes.goals import create_goal, update_goal
from matrix.api.schemas.goal import GoalCreate, GoalUpdate
from matrix.db.models import Goal as GoalORM


@pytest.mark.asyncio
async def test_create_second_active_goal_rejected(session, default_business, goal_fixture):
    """业务里已有一个 active PENDING goal 时，再创建应 409。"""
    await goal_fixture(target={"theme": "first"})

    body = GoalCreate(
        type="publish_note",
        target={"theme": "second"},
        business_id=default_business.id,
    )
    with pytest.raises(HTTPException) as exc_info:
        await create_goal(body, session)
    assert exc_info.value.status_code == 409
    assert "active goal" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_create_goal_allowed_when_existing_is_done(session, default_business, goal_fixture):
    """前一个 goal 已 DONE（achieved）时，可以创建新 goal。"""
    prev = await goal_fixture(target={"theme": "first"})
    prev.phase = "DONE"
    prev.status = "achieved"
    await session.flush()

    body = GoalCreate(
        type="publish_note",
        target={"theme": "second"},
        business_id=default_business.id,
    )
    result = await create_goal(body, session)
    assert result.target["theme"] == "second"


@pytest.mark.asyncio
async def test_create_goal_allowed_when_existing_is_cancelled(
    session, default_business, goal_fixture
):
    """前一个 goal 已 cancelled 时，可以创建新 goal。"""
    prev = await goal_fixture(target={"theme": "first"})
    prev.status = "cancelled"
    await session.flush()

    body = GoalCreate(
        type="publish_note",
        target={"theme": "second"},
        business_id=default_business.id,
    )
    result = await create_goal(body, session)
    assert result.target["theme"] == "second"


@pytest.mark.asyncio
async def test_create_goal_allowed_in_other_business(
    session, business_factory, goal_fixture
):
    """A 业务有 active goal，不影响在 B 业务创建 goal。"""
    biz_a = await business_factory(name="A", slug="biz-a-single")
    biz_b = await business_factory(name="B", slug="biz-b-single")

    await goal_fixture(target={"theme": "a-goal"}, business_id=biz_a.id)

    body = GoalCreate(
        type="publish_note",
        target={"theme": "b-goal"},
        business_id=biz_b.id,
    )
    result = await create_goal(body, session)
    assert result.business_id == biz_b.id


@pytest.mark.asyncio
async def test_update_reactivate_rejected_when_other_active_exists(
    session, default_business, goal_fixture
):
    """把一个 cancelled goal 重新激活，但业务里已有其他 active goal，应 409。"""
    active = await goal_fixture(target={"theme": "running"})
    active.phase = "EXECUTING"
    await session.flush()

    cancelled = await goal_fixture(target={"theme": "old"})
    cancelled.status = "cancelled"
    await session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await update_goal(cancelled.id, GoalUpdate(status="active"), session)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_update_reactivate_allowed_when_no_other_active(
    session, default_business, goal_fixture
):
    """业务里只有这一个 cancelled goal，重新激活应成功。"""
    cancelled = await goal_fixture(target={"theme": "old"})
    cancelled.status = "cancelled"
    await session.flush()

    result = await update_goal(cancelled.id, GoalUpdate(status="active"), session)
    assert result.status == "active"


@pytest.mark.asyncio
async def test_update_active_goal_fields_allowed(session, default_business, goal_fixture):
    """对当前 active goal 做非状态字段更新，不应触发单一 active 冲突。"""
    active = await goal_fixture(target={"theme": "running"})

    result = await update_goal(
        active.id,
        GoalUpdate(target_likes=1000, notes_per_round=2),
        session,
    )
    assert result.target_likes == 1000
    assert result.notes_per_round == 2
