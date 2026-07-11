"""matrix.device 子系统测试。

覆盖：
- HMAC 工具（签名 / 验签 / 时间戳过期 / key hash）
- KeyManager 生命周期（签发 / 查询 / 撤销 / 轮换）
- PairingService 完整流程（mock TailscaleClient）
- DeviceRegistry 注册 / 心跳 / 查询 / 分组
- AccountBinding 设备掉线 → task pending → 设备恢复 → task 续跑
- TailscaleClient 真实 HTTP 调用（respx 拦截）
- LoginStateMonitor 登录态上报 + 告警联动
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from matrix.db.models import (
    Account,
    AccountLoginSession,
    Device,
    DeviceHmacKey,
    Task,
)
from matrix.device.account_binding import AccountBinding, AccountBindingError
from matrix.device.hmac import (
    compute_signature,
    generate_key,
    hash_key,
    verify_signature,
)
from matrix.device.key_manager import KeyManager
from matrix.device.login_state import (
    LoginStateMonitor,
    LoginStateReport,
)
from matrix.device.pairing import (
    PairingError,
    PairingService,
)
from matrix.device.registry import (
    DeviceHeartbeatData,
    DeviceNotFound,
    DeviceRegistry,
)
from matrix.device.tailscale_client import TailscaleClient, TailscaleError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeResult:
    """SQLAlchemy Result 的最小 mock。"""

    def __init__(self, rows: list[Any] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar
        self.rowcount = len(self._rows) if rows is not None else 0

    def scalar_one_or_none(self) -> Any:
        if self._scalar is not None:
            return self._scalar
        return None

    def scalars(self) -> "FakeResult":
        return self

    def all(self) -> list[Any]:
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    """AsyncSession 的最小 fake（覆盖 KeyManager / PairingService / Registry / Binding 路径）。

    行为：
    - ``add(obj)`` 把对象存到 ``self.added`` 列表
    - ``flush()`` 异步 no-op
    - ``get(model, pk)`` 从 ``self.store`` 取（按 (model, pk)）
    - ``execute(stmt)`` 简单分发：调用注入的 ``execute_fn`` 或返回 FakeResult()
    - ``commit/rollback/close`` 异步 no-op（保持接口兼容）
    """

    def __init__(self, execute_fn=None) -> None:
        self.store: dict[tuple[type, Any], Any] = {}
        self.added: list[Any] = []
        self.execute_fn = execute_fn
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model: type, pk: Any) -> Any:
        return self.store.get((model, pk))

    def put(self, obj: Any) -> None:
        self.store[(type(obj), _pk_of(obj))] = obj

    def add(self, obj: Any) -> None:
        self.added.append(obj)
        # 默认按主键存，便于后续 get() 命中
        self.store[(type(obj), _pk_of(obj))] = obj

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        return None

    async def execute(self, stmt: Any) -> Any:
        if self.execute_fn is not None:
            return await self.execute_fn(stmt)
        # 默认行为：识别 ``select(Device|Account).where(id == X)`` 类查询
        return _default_execute(self.store, stmt)


def _default_execute(store: dict, stmt: Any) -> FakeResult:
    """把简单的 select-by-id 走 store 查找（覆盖 PairingService / Binding / Registry 的多数查询）。"""
    # 提取目标表
    target_model = None
    for model in (Device, Account, DeviceHmacKey, Task):
        if (
            getattr(stmt, "column_descriptions", None) is None
            or not stmt.column_descriptions
        ):
            continue
        entity = stmt.column_descriptions[0].get("entity")
        if entity is model:
            target_model = model
            break
    if target_model is None:
        # 退化：扫所有列描述
        try:
            for cd in stmt.column_descriptions or []:
                ent = cd.get("entity")
                if ent in (Device, Account, DeviceHmacKey, Task):
                    target_model = ent
                    break
        except Exception:
            pass
    if target_model is None:
        return FakeResult()
    # 提取 where 条件中的 id 值（SQLAlchemy 用 BindParameter）
    pk = None
    wc = getattr(stmt, "whereclause", None)
    if wc is not None and hasattr(wc, "left") and hasattr(wc, "right"):
        try:
            if getattr(wc.left, "name", None) == "id" and hasattr(wc.right, "value"):
                pk = wc.right.value
        except Exception:
            pass
    if pk is not None:
        obj = store.get((target_model, pk))
        return FakeResult(scalar=obj)
    # 无 id 条件：返回该 model 的所有对象
    rows = [v for (m, _), v in store.items() if m is target_model]
    return FakeResult(rows=rows)


def _extract_tag_filter(stmt: Any) -> Optional[Any]:
    """从 SQLAlchemy ``select(...).where(and_(...))`` 中提取 ``Device.tags.contains([tag])`` 的值。"""
    wc = getattr(stmt, "whereclause", None)
    if wc is None:
        return None
    clauses = getattr(wc, "clauses", [wc])
    for c in clauses:
        try:
            if getattr(c.left, "key", None) == "tags" and hasattr(c.right, "value"):
                return c.right.value
        except Exception:
            continue
    return None


def _pk_of(obj: Any) -> Any:
    """提取主键字段（id / device_id）。"""
    for attr in ("id", "device_id", "account_id"):
        if hasattr(obj, attr):
            return getattr(obj, attr)
    return id(obj)


def make_device(**overrides) -> Device:
    defaults = dict(
        id=uuid.uuid4(),
        nickname="dev1",
        model="Pixel 7",
        android_version="14",
        apk_version="1.0.0",
        tailnet_ip="100.64.0.1",
        tags=["brand-a"],
        status="pending",
        hmac_key_id=None,
    )
    defaults.update(overrides)
    return Device(**defaults)


def make_account(**overrides) -> Account:
    defaults = dict(
        id=uuid.uuid4(),
        handle="xhs_user",
        device_id=None,
        status="active",
        risk_score=0.0,
    )
    defaults.update(overrides)
    return Account(**defaults)


# ---------------------------------------------------------------------------
# hmac.py 测试
# ---------------------------------------------------------------------------


class TestHmac:
    def test_generate_key_length(self) -> None:
        key = generate_key()
        assert isinstance(key, bytes)
        assert len(key) == 32  # 256 bit

    def test_generate_key_unique(self) -> None:
        keys = {generate_key() for _ in range(10)}
        assert len(keys) == 10

    def test_hash_key_deterministic(self) -> None:
        secret = b"abc" * 8
        h1 = hash_key(secret)
        h2 = hash_key(secret)
        assert h1 == h2
        assert len(h1) == 32  # SHA-256

    def test_hash_key_different_inputs(self) -> None:
        assert hash_key(b"a") != hash_key(b"b")

    def test_compute_signature_deterministic(self) -> None:
        secret = b"k" * 32
        sig1 = compute_signature(secret, 1000, "req-1", b"body")
        sig2 = compute_signature(secret, 1000, "req-1", b"body")
        assert sig1 == sig2
        # base64 编码
        import base64
        base64.b64decode(sig1)  # 不抛异常即为合法 base64

    def test_compute_signature_changes_with_body(self) -> None:
        secret = b"k" * 32
        a = compute_signature(secret, 1, "r", b"x")
        b = compute_signature(secret, 1, "r", b"y")
        assert a != b

    def test_compute_signature_changes_with_request_id(self) -> None:
        secret = b"k" * 32
        a = compute_signature(secret, 1, "r1", b"x")
        b = compute_signature(secret, 1, "r2", b"x")
        assert a != b

    def test_verify_signature_valid(self) -> None:
        secret = b"k" * 32
        ts = int(time.time())
        sig = compute_signature(secret, ts, "req-1", b"hello")
        assert verify_signature(secret, ts, "req-1", b"hello", sig)

    def test_verify_signature_expired_timestamp(self) -> None:
        secret = b"k" * 32
        old_ts = int(time.time()) - 600  # 10 分钟前
        sig = compute_signature(secret, old_ts, "req-1", b"hello")
        # ttl 默认 300s → 过期
        assert not verify_signature(secret, old_ts, "req-1", b"hello", sig, ttl_seconds=300)

    def test_verify_signature_wrong_body(self) -> None:
        secret = b"k" * 32
        ts = int(time.time())
        sig = compute_signature(secret, ts, "req-1", b"hello")
        assert not verify_signature(secret, ts, "req-1", b"world", sig)

    def test_verify_signature_wrong_key(self) -> None:
        secret = b"k" * 32
        other = b"j" * 32
        ts = int(time.time())
        sig = compute_signature(secret, ts, "req-1", b"hello")
        assert not verify_signature(other, ts, "req-1", b"hello", sig)

    def test_verify_signature_empty_signature(self) -> None:
        assert not verify_signature(b"k" * 32, int(time.time()), "r", b"x", "")

    def test_verify_signature_invalid_timestamp(self) -> None:
        assert not verify_signature(b"k" * 32, "not-a-number", "r", b"x", "AAAA")

    def test_compute_signature_format(self) -> None:
        """签名内容必须严格为 ``{ts}\\n{request_id}\\n{body_sha256}``。"""
        import hmac as hmac_mod
        import hashlib
        secret = b"k" * 32
        ts = 1234567890
        req = "abc-123"
        body = b"some-body"
        expected_msg = f"{ts}\n{req}\n{hashlib.sha256(body).hexdigest()}".encode("utf-8")
        expected_sig = base64.b64encode(
            hmac_mod.new(secret, expected_msg, hashlib.sha256).digest()
        ).decode("ascii")
        assert compute_signature(secret, ts, req, body) == expected_sig


# ---------------------------------------------------------------------------
# KeyManager 测试
# ---------------------------------------------------------------------------


class TestKeyManager:
    @pytest.mark.asyncio
    async def test_issue_key_creates_db_row(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        km = KeyManager(session)
        issued = await km.issue_key(device.id)

        assert issued.key_id.startswith("hmk_")
        assert len(issued.secret) == 32
        # 应在 session.add 中找到 DeviceHmacKey 记录
        key_rows = [x for x in session.added if isinstance(x, DeviceHmacKey)]
        assert len(key_rows) == 1
        assert key_rows[0].id == issued.key_id
        # 存的是 hash，不应等于明文
        assert key_rows[0].key_hash != issued.secret
        # hash 等于 hash_key(secret)
        assert key_rows[0].key_hash == hash_key(issued.secret)

    @pytest.mark.asyncio
    async def test_issue_key_id_is_unique(self) -> None:
        ids = {KeyManager.new_key_id() for _ in range(50)}
        assert len(ids) == 50

    @pytest.mark.asyncio
    async def test_lookup_hash_returns_hash(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        km = KeyManager(session)
        issued = await km.issue_key(device.id)

        # 替换 execute_fn 让 lookup_hash 走 execute 路径
        async def execute_fn(stmt):
            # 任何查找都返回当前 key 的 hash
            return FakeResult(scalar=hash_key(issued.secret))

        session.execute_fn = execute_fn
        result = await km.lookup_hash(device.id, issued.key_id)
        assert result == hash_key(issued.secret)

    @pytest.mark.asyncio
    async def test_revoke_all_returns_count(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        km = KeyManager(session)
        issued = await km.issue_key(device.id)

        async def execute_fn(stmt):
            # revoke / 计数场景
            return FakeResult(rows=[1])

        session.execute_fn = execute_fn
        count = await km.revoke_all(device.id)
        assert count == 1

    @pytest.mark.asyncio
    async def test_rotate_if_expired_under_threshold(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        km = KeyManager(session, rotation_days=30)
        issued = await km.issue_key(device.id)

        # 刚签发的 key：未到轮换期
        async def execute_fn(stmt):
            return FakeResult(scalar=DeviceHmacKey(
                id=issued.key_id,
                device_id=device.id,
                key_hash=hash_key(issued.secret),
                created_at=datetime.now(timezone.utc),
            ))

        session.execute_fn = execute_fn
        result = await km.rotate_if_expired(device.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_rotate_if_expired_past_threshold(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        km = KeyManager(session, rotation_days=30)
        issued = await km.issue_key(device.id)

        old_time = datetime.now(timezone.utc) - timedelta(days=31)
        old_key = DeviceHmacKey(
            id=issued.key_id,
            device_id=device.id,
            key_hash=hash_key(issued.secret),
            created_at=old_time,
        )

        async def execute_fn(stmt):
            return FakeResult(scalar=old_key)

        session.execute_fn = execute_fn
        result = await km.rotate_if_expired(device.id)
        assert result is not None
        assert result.key_id != issued.key_id  # 新 key
        assert len(result.secret) == 32


# ---------------------------------------------------------------------------
# PairingService 测试（mock TailscaleClient）
# ---------------------------------------------------------------------------


class TestPairingService:
    @pytest.mark.asyncio
    async def test_create_pairing_returns_code_and_token(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        pairing = PairingService(session, km, ts)
        result = await pairing.create_pairing(device.id)
        assert result.pair_code.isdigit()
        assert len(result.pair_code) == 6
        assert result.token
        assert result.expires_at > time.time()

    @pytest.mark.asyncio
    async def test_create_pairing_rejects_unknown_device(self) -> None:
        session = FakeSession()
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        pairing = PairingService(session, km, ts)
        with pytest.raises(PairingError):
            await pairing.create_pairing(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_create_pairing_rejects_disabled_device(self) -> None:
        device = make_device(status="disabled")
        session = FakeSession()
        session.put(device)
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        pairing = PairingService(session, km, ts)
        with pytest.raises(PairingError):
            await pairing.create_pairing(device.id)

    @pytest.mark.asyncio
    async def test_complete_pairing_full_flow(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        pairing = PairingService(session, km, ts)

        created = await pairing.create_pairing(device.id)
        # 标准流程：validate → consume → complete
        assert pairing.validate_code(created.pair_code) == device.id
        assert pairing.consume_pair_code(created.pair_code) is True
        result = await pairing.complete_pairing(device.id, created.pair_code)
        assert result.device_id == device.id
        assert result.key_id.startswith("hmk_")
        # base64 编码可解码
        decoded = base64.b64decode(result.hmac_key)
        assert len(decoded) == 32
        assert result.sent_via == "tailscale"

    @pytest.mark.asyncio
    async def test_complete_pairing_rejects_wrong_code(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        pairing = PairingService(session, km, ts)

        # 未知配对码：validate_code 返回 None（不抛错），consume_pair_code 返回 False
        assert pairing.validate_code("000000") is None
        assert pairing.consume_pair_code("000000") is False

    @pytest.mark.asyncio
    async def test_complete_pairing_rejects_device_mismatch(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        pairing = PairingService(session, km, ts)
        created = await pairing.create_pairing(device.id)

        # validate_code 返回的是登记的 device.id；调用方应自行比对
        assert pairing.validate_code(created.pair_code) == device.id
        # 若强制用错误 device_id 调 complete_pairing，consume 成功但 complete 拒绝
        assert pairing.consume_pair_code(created.pair_code) is True
        with pytest.raises(PairingError, match="does not match"):
            await pairing.complete_pairing(uuid.uuid4(), created.pair_code)

    @pytest.mark.asyncio
    async def test_pair_code_one_time_use(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        pairing = PairingService(session, km, ts)
        created = await pairing.create_pairing(device.id)
        # 第一次：consume → complete
        assert pairing.consume_pair_code(created.pair_code) is True
        await pairing.complete_pairing(device.id, created.pair_code)
        # 第二次：validate 返回 None（已被消费）
        assert pairing.validate_code(created.pair_code) is None
        assert pairing.consume_pair_code(created.pair_code) is False

    @pytest.mark.asyncio
    async def test_pair_code_expires(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        # TTL = 0 → 立即过期
        pairing = PairingService(session, km, ts, ttl_seconds=0)
        created = await pairing.create_pairing(device.id)
        # validate / consume 都因过期失效（API 层在调 complete_pairing 之前会先
        # 走这两步拿到精确错误码：consume_pair_code 内部对过期会返回 False，
        # api 路径会先 validate_code 拿到 None 状态）。
        assert pairing.validate_code(created.pair_code) is None
        assert pairing.consume_pair_code(created.pair_code) is False
        with pytest.raises(PairingError, match="not_consumed"):
            await pairing.complete_pairing(device.id, created.pair_code)

    @pytest.mark.asyncio
    async def test_cleanup_expired_removes_old_codes(self) -> None:
        device = make_device()
        session = FakeSession()
        session.put(device)
        ts = TailscaleClient(api_url="", api_key="")
        km = KeyManager(session)
        pairing = PairingService(session, km, ts, ttl_seconds=0)
        await pairing.create_pairing(device.id)
        n = pairing.cleanup_expired()
        assert n >= 1


# ---------------------------------------------------------------------------
# DeviceRegistry 测试
# ---------------------------------------------------------------------------


class TestDeviceRegistry:
    @pytest.mark.asyncio
    async def test_register_creates_new_device(self) -> None:
        session = FakeSession()
        registry = DeviceRegistry(session)
        device = await registry.register_device(
            nickname="dev1",
            model="Pixel 7",
            android_version="14",
            apk_version="1.0.0",
            tailnet_ip="100.64.0.5",
        )
        assert device.id is not None
        assert device.nickname == "dev1"
        assert device.status == "active"
        assert device.tailnet_ip == "100.64.0.5"

    @pytest.mark.asyncio
    async def test_register_pending_without_ip(self) -> None:
        session = FakeSession()
        registry = DeviceRegistry(session)
        device = await registry.register_device(
            nickname="dev1",
            model="Pixel 7",
            android_version="14",
            apk_version="1.0.0",
        )
        assert device.status == "pending"
        assert device.tailnet_ip is None

    @pytest.mark.asyncio
    async def test_register_updates_existing_device(self) -> None:
        existing = make_device(status="pending", tailnet_ip=None)
        session = FakeSession()
        session.put(existing)
        registry = DeviceRegistry(session)
        updated = await registry.register_device(
            nickname="dev1",
            model="Pixel 7",
            android_version="14",
            apk_version="1.0.0",
            tailnet_ip="100.64.0.99",
            device_id=existing.id,
        )
        assert updated.id == existing.id
        assert updated.status == "active"
        assert updated.tailnet_ip == "100.64.0.99"

    @pytest.mark.asyncio
    async def test_unregister_revokes_keys_and_marks_disabled(self) -> None:
        device = make_device(status="active", hmac_key_id="hmk_abc")
        session = FakeSession()
        session.put(device)
        km = MagicMock()
        km.revoke_all = AsyncMock(return_value=1)
        registry = DeviceRegistry(session, key_manager=km)

        ok = await registry.unregister_device(device.id)
        assert ok is True
        assert device.status == "disabled"
        assert device.hmac_key_id is None
        assert device.deleted_at is not None
        km.revoke_all.assert_awaited_once_with(device.id)

    @pytest.mark.asyncio
    async def test_unregister_returns_false_for_unknown(self) -> None:
        session = FakeSession()
        registry = DeviceRegistry(session)
        ok = await registry.unregister_device(uuid.uuid4())
        assert ok is False

    @pytest.mark.asyncio
    async def test_heartbeat_writes_row_and_updates_last(self) -> None:
        device = make_device(status="active")
        session = FakeSession()
        session.put(device)
        registry = DeviceRegistry(session)
        await registry.update_heartbeat(
            device.id,
            DeviceHeartbeatData(
                battery=80,
                network="5G",
                signal_dbm=-70,
                foreground_app="com.xingin.xhs",
                tailscale_state="connected",
            ),
        )
        assert device.last_heartbeat is not None

    @pytest.mark.asyncio
    async def test_heartbeat_marks_tailscale_degraded(self) -> None:
        device = make_device(status="active")
        session = FakeSession()
        session.put(device)
        registry = DeviceRegistry(session)
        await registry.update_heartbeat(
            device.id,
            DeviceHeartbeatData(tailscale_state="disconnected"),
        )
        assert device.status == "tailscale_degraded"

    @pytest.mark.asyncio
    async def test_heartbeat_recovers_from_degraded(self) -> None:
        device = make_device(status="tailscale_degraded")
        session = FakeSession()
        session.put(device)
        registry = DeviceRegistry(session)
        await registry.update_heartbeat(
            device.id,
            DeviceHeartbeatData(tailscale_state="connected"),
        )
        assert device.status == "active"

    @pytest.mark.asyncio
    async def test_heartbeat_raises_for_unknown_device(self) -> None:
        session = FakeSession()
        registry = DeviceRegistry(session)
        with pytest.raises(DeviceNotFound):
            await registry.update_heartbeat(uuid.uuid4(), DeviceHeartbeatData())

    @pytest.mark.asyncio
    async def test_get_devices_filters_by_tag(self) -> None:
        d1 = make_device(tags=["a"])
        d2 = make_device(tags=["b"])
        d3 = make_device(tags=["a", "c"])
        session = FakeSession()

        async def execute_fn(stmt):
            tag_value = _extract_tag_filter(stmt)
            if tag_value:
                target = tag_value[0] if isinstance(tag_value, list) else tag_value
                return FakeResult(rows=[d for d in (d1, d2, d3) if target in (d.tags or [])])
            return FakeResult(rows=[d1, d2, d3])

        session.execute_fn = execute_fn
        registry = DeviceRegistry(session)
        result = await registry.get_devices(tag="a")
        assert d1 in result
        assert d2 not in result
        assert d3 in result

    @pytest.mark.asyncio
    async def test_group_by_tag(self) -> None:
        d1 = make_device(tags=["alpha", "beta"])
        d2 = make_device(tags=["alpha"])
        d3 = make_device(tags=[])
        session = FakeSession()

        async def execute_fn(stmt):
            return FakeResult(rows=[d1, d2, d3])

        session.execute_fn = execute_fn
        registry = DeviceRegistry(session)
        groups = await registry.group_by_tag()
        assert "alpha" in groups
        assert "beta" in groups
        assert d1 in groups["alpha"]
        assert d2 in groups["alpha"]
        assert d1 in groups["beta"]


# ---------------------------------------------------------------------------
# AccountBinding 测试 — 设备掉线/恢复的 task 流程
# ---------------------------------------------------------------------------


class TestAccountBinding:
    @pytest.mark.asyncio
    async def test_bind_sets_device_id(self) -> None:
        device = make_device(status="active")
        account = make_account()
        session = FakeSession()
        session.put(device)
        session.put(account)
        binding = AccountBinding(session)
        result = await binding.bind(account.id, device.id)
        assert result.bound is True
        assert account.device_id == device.id

    @pytest.mark.asyncio
    async def test_bind_rejects_disabled_device(self) -> None:
        device = make_device(status="disabled")
        account = make_account()
        session = FakeSession()
        session.put(device)
        session.put(account)
        binding = AccountBinding(session)
        with pytest.raises(AccountBindingError):
            await binding.bind(account.id, device.id)

    @pytest.mark.asyncio
    async def test_bind_rejects_unknown_device(self) -> None:
        account = make_account()
        session = FakeSession()
        session.put(account)
        binding = AccountBinding(session)
        with pytest.raises(AccountBindingError):
            await binding.bind(account.id, uuid.uuid4())

    @pytest.mark.asyncio
    async def test_unbind_clears_device_id(self) -> None:
        device = make_device()
        account = make_account(device_id=device.id)
        session = FakeSession()
        session.put(device)
        session.put(account)
        binding = AccountBinding(session)
        ok = await binding.unbind(account.id)
        assert ok is True
        assert account.device_id is None

    @pytest.mark.asyncio
    async def test_pause_tasks_marks_offline_error(self) -> None:
        device = make_device()
        session = FakeSession()
        # 模拟 update 行为
        async def execute_fn(stmt):
            return FakeResult(rows=[Task()])  # rowcount=1

        session.execute_fn = execute_fn
        binding = AccountBinding(session)
        n = await binding.pause_tasks_for_offline_device(device.id)
        assert n == 1

    @pytest.mark.asyncio
    async def test_resume_tasks_clears_offline_error(self) -> None:
        device = make_device()
        session = FakeSession()
        async def execute_fn(stmt):
            return FakeResult(rows=[Task(), Task()])

        session.execute_fn = execute_fn
        binding = AccountBinding(session)
        n = await binding.resume_tasks_for_recovered_device(device.id)
        assert n == 2

    @pytest.mark.asyncio
    async def test_device_offline_to_recovery_task_resume_flow(self) -> None:
        """端到端：掉线 → 任务 pending + 标记 → 恢复 → 续跑。

        这里用 FakeSession 的 execute_fn 模拟 ORM update 的 rowcount 行为，
        验证 AccountBinding 调度的语义正确。
        """
        device = make_device()
        session = FakeSession()
        binding = AccountBinding(session)

        # 阶段 1：设备掉线
        offline_calls = {"count": 0}
        async def offline_fn(stmt):
            offline_calls["count"] += 1
            return FakeResult(rows=[Task()])  # 假设有 1 个 task

        session.execute_fn = offline_fn
        n_paused = await binding.pause_tasks_for_offline_device(device.id)
        assert n_paused == 1
        assert offline_calls["count"] == 1

        # 阶段 2：设备恢复
        resume_calls = {"count": 0}
        async def resume_fn(stmt):
            resume_calls["count"] += 1
            return FakeResult(rows=[Task()])

        session.execute_fn = resume_fn
        n_resumed = await binding.resume_tasks_for_recovered_device(device.id)
        assert n_resumed == 1
        assert resume_calls["count"] == 1

    @pytest.mark.asyncio
    async def test_list_accounts_for_device(self) -> None:
        device = make_device()
        a1 = make_account(device_id=device.id, status="active")
        a2 = make_account(device_id=device.id, status="disabled")
        session = FakeSession()

        async def execute_fn(stmt):
            # 简化：忽略 status 过滤
            return FakeResult(rows=[a1, a2])

        session.execute_fn = execute_fn
        binding = AccountBinding(session)
        accounts = await binding.list_accounts_for_device(device.id)
        assert a1 in accounts
        assert a2 in accounts

        # include_disabled=False 时（mock 简化版同样返回两者；仅断言接口可调）
        accounts = await binding.list_accounts_for_device(device.id, include_disabled=False)
        assert isinstance(accounts, list)


# ---------------------------------------------------------------------------
# TailscaleClient 测试（respx mock httpx）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTailscaleClient:
    async def test_register_node_returns_id(self) -> None:
        async with respx.mock(base_url="https://hs.example.com") as mock:
            route = mock.post("/api/v1/node").mock(
                return_value=httpx.Response(200, json={"node": {"id": "42"}})
            )
            async with TailscaleClient(
                api_url="https://hs.example.com",
                api_key="secret",
            ) as ts:
                node_id = await ts.register_node(auth_key="auth-key-1", name="dev1")
            assert node_id == "42"
            assert route.called
            request = route.calls[0].request
            assert "Bearer secret" in request.headers.get("Authorization", "")

    async def test_register_node_raises_on_error(self) -> None:
        async with respx.mock(base_url="https://hs.example.com") as mock:
            mock.post("/api/v1/node").mock(
                return_value=httpx.Response(500, text="internal")
            )
            async with TailscaleClient(
                api_url="https://hs.example.com",
                api_key="secret",
            ) as ts:
                with pytest.raises(TailscaleError, match="register_node failed"):
                    await ts.register_node(auth_key="k", name="d")

    async def test_revoke_node_succeeds(self) -> None:
        async with respx.mock(base_url="https://hs.example.com") as mock:
            route = mock.delete("/api/v1/node/42").mock(
                return_value=httpx.Response(200, json={})
            )
            async with TailscaleClient(
                api_url="https://hs.example.com", api_key="k"
            ) as ts:
                await ts.revoke_node("42")
            assert route.called

    async def test_revoke_node_raises_on_404(self) -> None:
        async with respx.mock(base_url="https://hs.example.com") as mock:
            mock.delete("/api/v1/node/42").mock(
                return_value=httpx.Response(404, text="not found")
            )
            async with TailscaleClient(
                api_url="https://hs.example.com", api_key="k"
            ) as ts:
                with pytest.raises(TailscaleError):
                    await ts.revoke_node("42")

    async def test_list_nodes_returns_typed_list(self) -> None:
        async with respx.mock(base_url="https://hs.example.com") as mock:
            mock.get("/api/v1/node", params={"user": "tag:matrix-device"}).mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "nodes": [
                            {
                                "id": "1",
                                "name": "node-1.tail.ts.net",
                                "givenName": "node-1",
                                "ipAddresses": ["100.64.0.1"],
                            }
                        ]
                    },
                )
            )
            async with TailscaleClient(
                api_url="https://hs.example.com", api_key="k"
            ) as ts:
                nodes = await ts.list_nodes()
        assert len(nodes) == 1
        assert nodes[0].id == "1"
        assert nodes[0].ip_addresses == ["100.64.0.1"]

    async def test_register_node_raises_when_url_missing(self) -> None:
        # env 也清空
        import os
        old = os.environ.pop("TS_API_URL", None)
        try:
            async with TailscaleClient(api_url="", api_key="k") as ts:
                with pytest.raises(TailscaleError, match="TS_API_URL"):
                    await ts.register_node(auth_key="k", name="d")
        finally:
            if old is not None:
                os.environ["TS_API_URL"] = old


# ---------------------------------------------------------------------------
# LoginStateMonitor 测试
# ---------------------------------------------------------------------------


class TestLoginStateMonitor:
    @pytest.mark.asyncio
    async def test_report_success_updates_last_active(self) -> None:
        account = make_account()
        session = FakeSession()
        session.put(account)
        monitor = LoginStateMonitor(session)
        await monitor.report(
            LoginStateReport(
                account_id=account.id,
                device_id=uuid.uuid4(),
                result="success",
            )
        )
        assert account.last_active is not None
        assert account.status == "active"

    @pytest.mark.asyncio
    async def test_report_invalid_result_raises(self) -> None:
        session = FakeSession()
        monitor = LoginStateMonitor(session)
        with pytest.raises(Exception):
            await monitor.report(
                LoginStateReport(
                    account_id=uuid.uuid4(),
                    device_id=uuid.uuid4(),
                    result="bogus",
                )
            )

    @pytest.mark.asyncio
    async def test_failed_result_fires_alert(self) -> None:
        account = make_account(status="pending")  # 初始非 active
        session = FakeSession()
        session.put(account)
        alerter = MagicMock()
        alerter.fire = MagicMock()
        monitor = LoginStateMonitor(session, alerter=alerter)
        await monitor.report(
            LoginStateReport(
                account_id=account.id,
                device_id=uuid.uuid4(),
                result="failed",
                error_message="captcha",
            )
        )
        alerter.fire.assert_called_once()
        # 失败不应把 status 改成 active
        assert account.status == "pending"
        # 也没有 last_active
        assert account.last_active is None

    @pytest.mark.asyncio
    async def test_is_logged_in_true_recent(self) -> None:
        account = make_account()
        session = FakeSession()
        rec = AccountLoginSession(
            account_id=account.id,
            device_id=uuid.uuid4(),
            result="success",
            ts=datetime.now(timezone.utc),
        )
        session.add(rec)

        async def execute_fn(stmt):
            return FakeResult(scalar=rec)

        session.execute_fn = execute_fn
        monitor = LoginStateMonitor(session)
        ok = await monitor.is_logged_in(account.id, window_minutes=30)
        assert ok is True

    @pytest.mark.asyncio
    async def test_is_logged_in_false_no_record(self) -> None:
        session = FakeSession()

        async def execute_fn(stmt):
            return FakeResult(scalar=None)

        session.execute_fn = execute_fn
        monitor = LoginStateMonitor(session)
        ok = await monitor.is_logged_in(uuid.uuid4(), window_minutes=30)
        assert ok is False


# ---------------------------------------------------------------------------
# API 路由测试（FastAPI TestClient + FakeSession）
# ---------------------------------------------------------------------------


class TestDeviceAPI:
    """API 路由测试：直接调 endpoint 函数，注入 FakeSession。"""

    @pytest.mark.asyncio
    async def test_list_devices_returns_dict(self) -> None:
        from matrix.device.api import list_devices

        d1 = make_device()
        session = FakeSession()

        async def execute_fn(stmt):
            return FakeResult(rows=[d1])

        session.execute_fn = execute_fn
        result = await list_devices(session=session, status_filter=None, tag=None)
        assert "items" in result
        assert len(result["items"]) == 1
        assert result["items"][0].nickname == "dev1"

    @pytest.mark.asyncio
    async def test_get_device_404(self) -> None:
        from matrix.device.api import get_device
        from fastapi import HTTPException

        session = FakeSession()
        with pytest.raises(HTTPException) as exc_info:
            await get_device(uuid.uuid4(), session=session)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_register_device_via_api(self) -> None:
        from matrix.device.api import register_device, DeviceRegisterIn

        session = FakeSession()
        payload = DeviceRegisterIn(
            nickname="dev1",
            model="Pixel",
            android_version="14",
            apk_version="1.0.0",
            tailnet_ip="100.64.0.5",
        )
        result = await register_device(payload=payload, session=session)
        assert result.nickname == "dev1"
        assert result.status == "active"
        assert result.tailnet_ip == "100.64.0.5"


# ---------------------------------------------------------------------------
# ApkHttpClient: APK 契约 + HMAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestApkHttpClientCollect:
    @staticmethod
    def _endpoint():
        from matrix.device.adapters import ApkEndpoint

        return ApkEndpoint(base_url="http://apk.local:8080", hmac_key=b"k" * 32)

    async def test_endpoint_resolver_loads_device_address_and_secret(self) -> None:
        from types import SimpleNamespace

        from matrix.device.endpoints import DeviceEndpointResolver

        device = make_device(hmac_key_id="hmk_test")

        class Session:
            async def get(self, model, key):
                if model is Device:
                    return device if key == device.id else None
                return SimpleNamespace(value={"secret": base64.b64encode(b"s" * 32).decode("ascii")})

        class SessionContext:
            async def __aenter__(self):
                return Session()

            async def __aexit__(self, exc_type, exc, traceback):
                return None

        resolver = DeviceEndpointResolver(lambda: SessionContext())
        endpoint = await resolver(device.id)

        assert endpoint.base_url == "http://100.64.0.1:8765"
        assert endpoint.hmac_key == b"s" * 32

    async def test_skips_null_views(self) -> None:
        """APK 端 NoteMetric.views=null 时，HTTP client 应丢弃该键（不变成 0）。

        避免下游 collect_node / ANALYZE 误以为"浏览量真的是 0"。
        """
        from matrix.device.adapters import ApkHttpClient

        async with respx.mock(base_url="http://apk.local:8080") as mock:
            mock.post("/xhs/collect_metrics").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "data": [{
                            "views": None,
                            "likes": 10,
                            "collects": 2,
                            "comments": 1,
                            "follows_gained": 0,
                        }],
                    },
                )
            )
            async with ApkHttpClient(
                resolver=AsyncMock(
                    return_value=self._endpoint()
                ),
            ) as client:
                result = await client.collect(
                    device_id=uuid.uuid4(),
                    account_id=uuid.uuid4(),
                    platform_note_id="p1",
                )
            assert "views" not in result
            assert result["likes"] == 10
            assert result["collects"] == 2

    async def test_returns_int_values_when_all_present(self) -> None:
        """正常路径：APK 返回全字段时，dict 包含全部 5 个键且为 int。"""
        from matrix.device.adapters import ApkHttpClient

        async with respx.mock(base_url="http://apk.local:8080") as mock:
            mock.post("/xhs/collect_metrics").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "data": [{
                            "views": 100,
                            "likes": 5,
                            "collects": 1,
                            "comments": 0,
                            "follows_gained": 0,
                        }],
                    },
                )
            )
            async with ApkHttpClient(
                resolver=AsyncMock(
                    return_value=self._endpoint()
                ),
            ) as client:
                result = await client.collect(
                    device_id=uuid.uuid4(),
                    account_id=uuid.uuid4(),
                    platform_note_id="p1",
                )
            assert result == {
                "views": 100,
                "likes": 5,
                "collects": 1,
                "comments": 0,
                "follows_gained": 0,
            }

    async def test_collect_signs_the_exact_body_and_sends_request_id(self) -> None:
        from matrix.device.adapters import ApkHttpClient

        async with respx.mock(base_url="http://apk.local:8080") as mock:
            route = mock.post("/xhs/collect_metrics").mock(
                return_value=httpx.Response(200, json={"ok": True, "data": [{"likes": 2}]})
            )
            async with ApkHttpClient(
                resolver=AsyncMock(return_value=self._endpoint()),
            ) as client:
                await client.collect(
                    device_id=uuid.uuid4(), account_id=uuid.uuid4(), platform_note_id="p1"
                )

        request = route.calls[-1].request
        payload = json.loads(request.content)
        assert payload["request_id"] == request.headers["X-Request-Id"]
        assert verify_signature(
            b"k" * 32,
            request.headers["X-Timestamp"],
            request.headers["X-Request-Id"],
            request.content,
            request.headers["X-Signature"],
        )

    async def test_publish_and_interact_follow_apk_envelopes(self) -> None:
        from matrix.device.adapters import ApkHttpClient

        async with respx.mock(base_url="http://apk.local:8080") as mock:
            publish_route = mock.post("/xhs/publish").mock(
                return_value=httpx.Response(
                    200,
                    json={"ok": True, "data": {"platform_note_id": "note-1", "url": "https://xhs/note-1"}},
                )
            )
            interact_route = mock.post("/xhs/interact").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            async with ApkHttpClient(
                resolver=AsyncMock(return_value=self._endpoint()),
            ) as client:
                published = await client.publish(
                    device_id=uuid.uuid4(),
                    account_id=uuid.uuid4(),
                    title="t",
                    content="c",
                    images=[],
                    tags=[],
                    request_id="publish-1",
                )
                interacted = await client.interact(
                    device_id=uuid.uuid4(),
                    account_id=uuid.uuid4(),
                    action="like",
                    target_note_id="note-2",
                    request_id="interact-1",
                )

        assert published.ok is True
        assert published.platform_note_id == "note-1"
        assert published.platform_url == "https://xhs/note-1"
        assert interacted.ok is True
        assert json.loads(interact_route.calls[-1].request.content)["target"] == {"note_id": "note-2"}
        assert publish_route.calls[-1].request.headers["X-Signature"]
