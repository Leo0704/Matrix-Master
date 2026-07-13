"""P2-3 设备注册回归：register 时只需 nickname，其他字段 null。

避免老板添加设备时还要手填 4 个会被 APK 实际值覆盖的字段。
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_register_with_only_nickname_persists_nulls():
    from matrix.api.routes.devices import register_device
    from matrix.db.models import Device as DeviceORM

    body_mock = MagicMock()
    body_mock.nickname = "minimal-device"
    body_mock.adb_serial = None

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    # Mock _to_schema 避免 pydantic 校验 MagicMock 的字段（id 等）
    fake_schema = MagicMock()
    fake_schema.pair_code = "123456"

    with patch(
        "matrix.api.routes.devices._issue_pair_code", return_value="123456"
    ), patch(
        "matrix.api.routes.devices._to_schema", return_value=fake_schema
    ) as to_schema_mock:
        result = await register_device(body=body_mock, session=session)

    # 只有一次 add —— DeviceORM
    assert session.add.call_count == 1
    added = session.add.call_args[0][0]
    assert isinstance(added, DeviceORM)
    assert added.nickname == "minimal-device"
    # 4 字段都没填（None）
    assert added.model is None
    assert added.android_version is None
    assert added.apk_version is None
    assert added.tailnet_ip is None
    assert added.adb_serial is None
    assert added.status == "pending"
    assert added.tags == []

    assert result.pair_code == "123456"
    to_schema_mock.assert_called_once()


@pytest.mark.asyncio
async def test_register_with_adb_serial_keeps_it():
    from matrix.api.routes.devices import register_device
    from matrix.db.models import Device as DeviceORM

    body_mock = MagicMock()
    body_mock.nickname = "minimal-with-adb"
    body_mock.adb_serial = "12ab34cd"

    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    fake_schema = MagicMock()
    fake_schema.pair_code = "654321"

    with patch(
        "matrix.api.routes.devices._issue_pair_code", return_value="654321"
    ), patch(
        "matrix.api.routes.devices._to_schema", return_value=fake_schema
    ):
        result = await register_device(body=body_mock, session=session)

    added = session.add.call_args[0][0]
    assert isinstance(added, DeviceORM)
    assert added.adb_serial == "12ab34cd"
    # 4 字段依然 None
    assert added.model is None
    assert added.android_version is None
    assert added.apk_version is None
    assert added.tailnet_ip is None
    assert result.pair_code == "654321"
