"""账号激活入口测试（v0.7+ 业务模型重构）。

覆盖：
- pending → active 成功
- 非法状态转移返回 409
- banned 状态不允许 API 直改
- 激活后账号出现在 ``status=active`` 列表过滤里

测试直接调用路由函数，避免启动 lifespan 里的后台 worker/scheduler。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from matrix.api.routes.accounts import list_accounts, update_account
from matrix.api.schemas.account import AccountUpdate
from matrix.db.models import Account as AccountORM


@pytest.mark.asyncio
async def test_activate_pending_account_success(session, default_business, account_fixture):
    """pending 账号可通过 PATCH 激活为 active。"""
    acct = await account_fixture(handle="@activate-me", business_id=default_business.id)
    assert acct.status == "pending"

    updated = await update_account(
        acct.id,
        AccountUpdate(status="active"),
        session=session,
    )

    assert updated.status == "active"


@pytest.mark.asyncio
async def test_suspend_active_account_success(session, default_business, account_fixture):
    """active 账号可被暂停为 suspended。"""
    acct = await account_fixture(handle="@suspend-me", business_id=default_business.id)
    acct.status = "active"
    await session.flush()

    updated = await update_account(
        acct.id,
        AccountUpdate(status="suspended"),
        session=session,
    )

    assert updated.status == "suspended"


@pytest.mark.asyncio
async def test_reactivate_suspended_account_success(session, default_business, account_fixture):
    """suspended 账号可恢复为 active。"""
    acct = await account_fixture(handle="@reactivate-me", business_id=default_business.id)
    acct.status = "suspended"
    await session.flush()

    updated = await update_account(
        acct.id,
        AccountUpdate(status="active"),
        session=session,
    )

    assert updated.status == "active"


@pytest.mark.asyncio
async def test_disable_active_account_success(session, default_business, account_fixture):
    """任意非 banned 状态可 disabled。"""
    acct = await account_fixture(handle="@disable-me", business_id=default_business.id)
    acct.status = "active"
    await session.flush()

    updated = await update_account(
        acct.id,
        AccountUpdate(status="disabled"),
        session=session,
    )

    assert updated.status == "disabled"


@pytest.mark.asyncio
async def test_invalid_status_transition_returns_409(session, default_business, account_fixture):
    """不允许的转移返回 409 + 可读 message。"""
    acct = await account_fixture(handle="@invalid-transition", business_id=default_business.id)
    acct.status = "active"
    await session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await update_account(
            acct.id,
            AccountUpdate(status="pending"),
            session=session,
        )

    assert exc_info.value.status_code == 409
    assert "invalid status transition" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_banned_status_cannot_be_changed_via_api(session, default_business, account_fixture):
    """banned 状态不允许 API 直改（离开 banned 也禁止）。"""
    acct = await account_fixture(handle="@banned-stays", business_id=default_business.id)
    acct.status = "banned"
    await session.flush()

    with pytest.raises(HTTPException) as exc_info:
        await update_account(
            acct.id,
            AccountUpdate(status="active"),
            session=session,
        )

    assert exc_info.value.status_code == 409
    assert "banned" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_cannot_set_status_to_banned_via_api(session, default_business, account_fixture):
    """API 不能直接把账号设为 banned。"""
    acct = await account_fixture(handle="@no-banned", business_id=default_business.id)

    with pytest.raises(HTTPException) as exc_info:
        await update_account(
            acct.id,
            AccountUpdate(status="banned"),
            session=session,
        )

    assert exc_info.value.status_code == 409
    assert "banned" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_active_account_appears_in_status_filter(session, default_business, account_fixture):
    """激活后账号能被 ``GET /accounts?status=active`` 过滤出来。"""
    acct = await account_fixture(handle="@list-active", business_id=default_business.id)
    await update_account(
        acct.id,
        AccountUpdate(status="active"),
        session=session,
    )

    resp = await list_accounts(
        device_id=None,
        persona_id=None,
        status_filter="active",
        business_id=default_business.id,
        session=session,
    )

    assert any(item.id == acct.id for item in resp.items)


@pytest.mark.asyncio
async def test_idempotent_same_status_no_error(session, default_business, account_fixture):
    """同状态再传一次视为幂等，不报错。"""
    acct = await account_fixture(handle="@idempotent", business_id=default_business.id)
    acct.status = "active"
    await session.flush()

    updated = await update_account(
        acct.id,
        AccountUpdate(status="active"),
        session=session,
    )

    assert updated.status == "active"
