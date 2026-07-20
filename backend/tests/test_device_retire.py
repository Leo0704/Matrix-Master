"""设备退役（retire）端点测试。"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from starlette.requests import Request

from matrix.api.routes.devices import retire_device
from matrix.db.models import (
    Account as AccountORM,
    Device as DeviceORM,
    DeviceHmacKey as DeviceHmacKeyORM,
)
from matrix.device.api import verify_hmac


@pytest.mark.asyncio
async def test_retire_clears_account_binding_and_disables_device(
    session, default_business, account_fixture, device_fixture
):
    """退役设备：账号 device_id 清空，设备 status=disabled。"""
    device = await device_fixture(business_id=default_business.id)
    device.status = "active"
    account = await account_fixture(handle="@retire", business_id=default_business.id)
    # 把账号绑到设备上
    account.device_id = device.id
    await session.flush()

    res = await retire_device(device.id, session)

    assert res.device_id == device.id
    assert res.unbound_account_handle == "@retire"
    assert account.device_id is None
    assert device.status == "disabled"


@pytest.mark.asyncio
async def test_retire_works_for_unbound_device(
    session, default_business, device_fixture
):
    """没绑账号的设备也能退役。"""
    device = await device_fixture(business_id=default_business.id)
    device.status = "active"
    await session.flush()

    res = await retire_device(device.id, session)

    assert res.device_id == device.id
    assert res.unbound_account_handle is None
    assert device.status == "disabled"


@pytest.mark.asyncio
async def test_retire_revokes_hmac_keys(
    session, default_business, device_fixture
):
    """退役时必须撤销该设备的所有 HMAC 密钥。"""
    device = await device_fixture(business_id=default_business.id)
    device.status = "active"
    key = DeviceHmacKeyORM(
        id="hmk_test",
        device_id=device.id,
        key_hash=b"hash",
    )
    session.add(key)
    device.hmac_key_id = "hmk_test"
    await session.flush()

    await retire_device(device.id, session)

    # 密钥被标记撤销，设备当前 key_id 被清空
    keys = (
        await session.execute(
            select(DeviceHmacKeyORM).where(
                DeviceHmacKeyORM.device_id == device.id
            )
        )
    ).scalars().all()
    assert len(keys) == 1
    assert keys[0].revoked_at is not None
    assert device.hmac_key_id is None


@pytest.mark.asyncio
async def test_retire_404_for_missing_device(session):
    """退役不存在的设备返回 404。"""
    with pytest.raises(HTTPException) as exc_info:
        await retire_device(uuid.uuid4(), session)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_verify_hmac_rejects_disabled_device(session, device_fixture):
    """disabled 设备即使还有 hmac_key_id，也无法通过 HMAC 鉴权。"""
    device = await device_fixture()
    device.status = "disabled"
    device.hmac_key_id = "hmk_still_present"
    await session.flush()

    async def _receive() -> dict:
        return {"type": "http.request", "body": b""}

    request = Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/devices/{device.id}/heartbeat",
            "headers": [
                (b"x-signature", b"sig"),
                (b"x-timestamp", b"1234567890"),
                (b"x-request-id", b"rid"),
            ],
        },
        receive=_receive,
    )

    with pytest.raises(HTTPException) as exc_info:
        await verify_hmac(
            request,
            device.id,
            session,
            x_signature="sig",
            x_timestamp="1234567890",
            x_request_id="rid",
        )
    assert exc_info.value.status_code == 401
    assert "retired" in str(exc_info.value.detail).lower()
