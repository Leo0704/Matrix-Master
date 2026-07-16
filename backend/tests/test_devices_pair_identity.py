"""P2-3 pair 端点接收 APK 自报身份，回写 Device 行的 4 字段。

回归 4 件事：
1. body.identity 不传：4 字段保持 None（老 APK 兼容）
2. body.identity 4 字段全填：4 字段都被写入 Device 行
3. body.identity 部分字段：只写非空字段（避免空字符串覆盖已有值）
4. status 从 pending 转 active 时同时返回成功
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_device_orm(
    *,
    nickname: str = "test-device",
    status: str = "pending",
    model: str | None = None,
    android_version: str | None = None,
    apk_version: str | None = None,
    tailnet_ip: str | None = None,
):
    d = SimpleNamespace()
    d.id = uuid.uuid4()
    d.nickname = nickname
    d.status = status
    d.hmac_key_id = None
    d.model = model
    d.android_version = android_version
    d.apk_version = apk_version
    d.tailnet_ip = tailnet_ip
    d.deleted_at = None
    return d


def _mock_session_with_device(d):
    s = MagicMock()
    s.get = AsyncMock(return_value=d)
    s.add = MagicMock()
    s.flush = AsyncMock()
    s.commit = AsyncMock()
    s.rollback = AsyncMock()
    return s


@pytest.mark.asyncio
async def test_pair_without_identity_leaves_fields_null():
    from matrix.api.routes.devices import pair_device

    d = _make_device_orm()
    s = _mock_session_with_device(d)
    body_mock = MagicMock(spec=["pair_code", "identity"])
    body_mock.pair_code = "000000"
    body_mock.identity = None

    with patch(
        "matrix.api.routes.devices._claim_pair_code", return_value=d.id
    ), patch(
        "matrix.api.routes.devices._finalize_pair_code"
    ), patch(
        "matrix.api.routes.devices._restore_pair_code"
    ), patch(
        "matrix.api.routes.devices.KeyManager"
    ) as km_cls:
        km = MagicMock()
        km.revoke_all = AsyncMock()
        km.issue_key = AsyncMock(
            return_value=SimpleNamespace(
                key_id="kid-1", secret=b"\x00" * 32
            )
        )
        km_cls.return_value = km

        await pair_device(
            device_id=d.id, body=body_mock, session=s
        )

    assert d.model is None
    assert d.android_version is None
    assert d.apk_version is None
    assert d.tailnet_ip is None
    assert d.status == "active"  # pending → active 仍然推进


@pytest.mark.asyncio
async def test_pair_with_identity_writes_4_fields():
    from matrix.api.routes.devices import pair_device

    d = _make_device_orm()
    s = _mock_session_with_device(d)
    body_mock = MagicMock(spec=["pair_code", "identity"])
    body_mock.pair_code = "111111"
    body_mock.identity = SimpleNamespace(
        model="Pixel 7",
        android_version="14",
        apk_version="0.4.0",
        tailnet_ip="100.64.0.42",
    )

    with patch(
        "matrix.api.routes.devices._claim_pair_code", return_value=d.id
    ), patch(
        "matrix.api.routes.devices._finalize_pair_code"
    ), patch(
        "matrix.api.routes.devices._restore_pair_code"
    ), patch(
        "matrix.api.routes.devices.KeyManager"
    ) as km_cls:
        km = MagicMock()
        km.revoke_all = AsyncMock()
        km.issue_key = AsyncMock(
            return_value=SimpleNamespace(
                key_id="kid-2", secret=b"\x00" * 32
            )
        )
        km_cls.return_value = km

        await pair_device(
            device_id=d.id, body=body_mock, session=s
        )

    assert d.model == "Pixel 7"
    assert d.android_version == "14"
    assert d.apk_version == "0.4.0"
    assert d.tailnet_ip == "100.64.0.42"
    assert d.status == "active"


@pytest.mark.asyncio
async def test_pair_partial_identity_does_not_overwrite_with_empty():
    """已经有 model 时，APK 这次只带 android_version 过来 → 不该把 model 清掉。"""
    from matrix.api.routes.devices import pair_device

    d = _make_device_orm(model="Pixel 7", android_version="13")
    s = _mock_session_with_device(d)
    body_mock = MagicMock(spec=["pair_code", "identity"])
    body_mock.pair_code = "222222"
    body_mock.identity = SimpleNamespace(
        model="",  # 空字符串不应该覆盖
        android_version="14",  # 真实更新
        apk_version="0.4.0",  # 新填
        tailnet_ip=None,  # None 不该写
    )

    with patch(
        "matrix.api.routes.devices._claim_pair_code", return_value=d.id
    ), patch(
        "matrix.api.routes.devices._finalize_pair_code"
    ), patch(
        "matrix.api.routes.devices._restore_pair_code"
    ), patch(
        "matrix.api.routes.devices.KeyManager"
    ) as km_cls:
        km = MagicMock()
        km.revoke_all = AsyncMock()
        km.issue_key = AsyncMock(
            return_value=SimpleNamespace(
                key_id="kid-3", secret=b"\x00" * 32
            )
        )
        km_cls.return_value = km

        await pair_device(
            device_id=d.id, body=body_mock, session=s
        )

    # 旧值守住
    assert d.model == "Pixel 7"        # 空串没覆盖
    # 真实更新
    assert d.android_version == "14"
    assert d.apk_version == "0.4.0"
    # None 没动
    assert d.tailnet_ip is None


@pytest.mark.asyncio
async def test_pair_db_failure_restores_code_and_reraises():
    """事务 commit 抛异常时：配对码必须被 restore（清掉领用标记），异常继续上抛。

    锁住原子性修复：避免“码被领用但 key 没下发”的不可恢复态。
    """
    from matrix.api.routes.devices import pair_device

    d = _make_device_orm()
    s = _mock_session_with_device(d)
    # 让 commit 抛异常，模拟 DB 事务失败。
    s.commit = AsyncMock(side_effect=RuntimeError("db connection lost"))
    body_mock = MagicMock(spec=["pair_code", "identity"])
    body_mock.pair_code = "333333"
    body_mock.identity = None

    with patch(
        "matrix.api.routes.devices._claim_pair_code", return_value=d.id
    ), patch(
        "matrix.api.routes.devices._finalize_pair_code"
    ) as finalize_mock, patch(
        "matrix.api.routes.devices._restore_pair_code"
    ) as restore_mock, patch(
        "matrix.api.routes.devices.KeyManager"
    ) as km_cls:
        km = MagicMock()
        km.revoke_all = AsyncMock()
        km.issue_key = AsyncMock(
            return_value=SimpleNamespace(key_id="kid-x", secret=b"\x00" * 32)
        )
        km_cls.return_value = km

        with pytest.raises(RuntimeError, match="db connection lost"):
            await pair_device(device_id=d.id, body=body_mock, session=s)

    # 失败路径：码被还原（可重试），绝不会被 finalize 删掉。
    restore_mock.assert_called_once_with("333333")
    finalize_mock.assert_not_called()
    # rollback 也被调用，保证事务清理。
    s.rollback.assert_awaited_once()
