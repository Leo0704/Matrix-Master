"""API 集成测试：httpx AsyncClient + mock DB session。

不连真实 DB：override ``get_db`` 依赖，返回一个 ``FakeAsyncSession``，能覆盖
路由用到的 session.get / execute / add / flush / commit / rollback / close。

测试用一个共享的 ``FakeDB``（内存表）作为多个 session 之间的"真实存储"，
这样跨请求的写操作也能被读到。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from matrix.api import deps
from matrix.api.app import create_app
from matrix.db.models import (
    AgentRun,
    Business,
    Device,
)


# ---------------------------------------------------------------------------
# FakeDB + FakeAsyncSession — 内存模拟 SQLAlchemy
# ---------------------------------------------------------------------------


class FakeDB:
    """多 session 共享的内存表（仅按 (cls, id) 索引的对象集合）。"""

    def __init__(self) -> None:
        self.store: dict[tuple[type, Any], Any] = {}


def _store_key(obj: Any) -> tuple[type, Any]:
    """FakeDB store 的键：默认 (type, obj.id)。

    主键不叫 ``id`` 的模型（如 ChatConfirmationToken 的 ``token``）回退到
    SQLAlchemy mapper 的第一个主键列名取值。
    """
    pk = getattr(obj, "id", None)
    if pk is None and not hasattr(obj, "id"):
        from sqlalchemy import inspect as sa_inspect

        pk = getattr(obj, sa_inspect(type(obj)).primary_key[0].name, None)
    return (type(obj), pk)


class _ScalarResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_ScalarResult":
        return self

    def unique(self) -> "_ScalarResult":
        # 真实 Result.unique() 用于 joinedload 去重；fake 无需去重，原样返回
        return self

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        if len(self._rows) != 1:
            raise ValueError("expected exactly one row")
        return self._rows[0]

    def scalar_one(self) -> Any:
        return self.one()

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeAsyncSession:
    """一个简化的 AsyncSession — 支持 add / flush / commit / rollback / get / execute。

    共享一个 ``FakeDB`` 实例，写操作通过 ``commit`` 提交到 db.store。
    """

    def __init__(self, db: FakeDB) -> None:
        self._db = db
        self.added: list[Any] = []
        self.rolled_back_flag = False
        self.closed = False
        self.committed = False

    # 配置 helper
    def seed(self, obj: Any) -> None:
        self._db.store[_store_key(obj)] = obj

    # Session 协议
    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                obj.id = uuid.uuid4()
            # 真实 DB 的 server_default(NOW()) 在 fake 里不会生效，
            # 响应 schema 的 created_at 是必填 datetime，这里补一个
            if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
                from datetime import UTC, datetime

                obj.created_at = datetime.now(UTC)
            key = _store_key(obj)
            if key[1] is not None:
                self._db.store[key] = obj

    async def commit(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                obj.id = uuid.uuid4()
            key = _store_key(obj)
            if key[1] is not None:
                self._db.store[key] = obj
        self.committed = True
        self.added.clear()

    async def rollback(self) -> None:
        self.rolled_back_flag = True
        self.added.clear()

    async def close(self) -> None:
        self.closed = True

    async def delete(self, obj: Any) -> None:
        # 从 db.store 真删
        self._db.store.pop(_store_key(obj), None)

    async def get(self, cls: type, pk: Any):
        obj = self._db.store.get((cls, pk))
        if obj is None:
            return None
        # 软删：deleted_at != None 视为不存在
        if getattr(obj, "deleted_at", None) is not None:
            return None
        return obj

    async def execute(self, stmt: Any):
        # 简化：直接对所有 ORM 对象按字段做内存过滤
        try:
            sql = str(stmt).lower()
        except Exception:
            sql = ""

        # 简化：标量聚合查询（count / sum / coalesce）→ 先处理（可能 entity 是 None）
        if any(agg in sql for agg in ("count(", "sum(", "coalesce(", "avg(", "min(", "max(")):
            return _ScalarResult([0])

        # 处理 Delete / Update 语句
        from sqlalchemy import Delete, Update

        if isinstance(stmt, Delete):
            return await self._execute_delete(stmt)
        if isinstance(stmt, Update):
            return await self._execute_update(stmt)

        # 简化策略：扫描 db.store 中所有 (Cls, id) 匹配 stmt 涉及的列
        from matrix.db.models import Base

        # 仅处理 Select 语句（带 column_descriptions 属性）
        if not hasattr(stmt, "column_descriptions"):
            return _ScalarResult([])

        # 找 select 的列对应的 ORM 类
        entities = stmt.column_descriptions
        orm_cls = None
        for ent in entities:
            ent_cls = ent.get("entity")
            if ent_cls is not None and isinstance(ent_cls, type):
                if issubclass(ent_cls, Base):
                    orm_cls = ent_cls
                    break
        if orm_cls is None:
            return _ScalarResult([])

        # 全表扫 + 软删过滤
        rows = [
            obj
            for (cls, _), obj in self._db.store.items()
            if cls is orm_cls and getattr(obj, "deleted_at", None) is None
        ]

        # ORDER BY
        order_by = getattr(stmt, "_order_by_clause", None)
        if order_by is not None:
            try:
                elem = order_by.element  # type: ignore[union-attr]
                col = elem.element  # type: ignore[union-attr]
                key = col.key
                desc = "desc" in str(order_by).lower()
                rows = sorted(
                    rows,
                    key=lambda r: getattr(r, key) or "",
                    reverse=desc,
                )
            except Exception:
                pass

        # LIMIT / OFFSET（解析靠 str() 截取）
        s = str(stmt)
        import re as _re

        m = _re.search(r"\blimit\s+(\d+)\b", s, _re.I)
        if m:
            rows = rows[: int(m.group(1))]
        m = _re.search(r"\boffset\s+(\d+)\b", s, _re.I)
        if m:
            rows = rows[int(m.group(1)) :]

        return _ScalarResult(rows)

    async def _execute_delete(self, stmt: Any) -> Any:
        """内存执行 Delete 语句，返回带 rowcount 的 mock result。"""
        orm_cls = self._orm_cls_from_stmt(stmt)
        if orm_cls is None:
            return _MockRowcountResult(0)

        deleted = 0
        for key, obj in list(self._db.store.items()):
            cls, _ = key
            if cls is not orm_cls:
                continue
            if getattr(obj, "deleted_at", None) is not None:
                continue
            if self._match_where(obj, stmt):
                del self._db.store[key]
                deleted += 1
        return _MockRowcountResult(deleted)

    async def _execute_update(self, stmt: Any) -> Any:
        """内存执行 Update 语句，返回带 rowcount 的 mock result。"""
        orm_cls = self._orm_cls_from_stmt(stmt)
        if orm_cls is None:
            return _MockRowcountResult(0)

        values = self._extract_update_values(stmt)
        updated = 0
        for (cls, _), obj in self._db.store.items():
            if cls is not orm_cls:
                continue
            if getattr(obj, "deleted_at", None) is not None:
                continue
            if self._match_where(obj, stmt):
                for k, v in values.items():
                    setattr(obj, k, v)
                updated += 1
        return _MockRowcountResult(updated)

    def _orm_cls_from_stmt(self, stmt: Any) -> type | None:
        """从 Delete/Update 语句的 table 推断 ORM 类。"""
        table = getattr(stmt, "table", None)
        if table is None:
            return None
        name = getattr(table, "name", None)
        if name is None:
            return None
        from matrix.db.models import Base

        for (cls, _), obj in self._db.store.items():
            if issubclass(cls, Base) and getattr(cls, "__tablename__", None) == name:
                return cls
        return None

    def _match_where(self, obj: Any, stmt: Any) -> bool:
        """极其简化的 where 匹配。"""
        s = str(stmt).lower()

        # id = uuid（单条删除）
        if "where notifications.id =" in s or "where " + obj.__tablename__ + ".id =" in s:
            import re as _re
            m = _re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", str(stmt))
            if m:
                return str(obj.id) == m.group(0)

        # read_at IS NOT NULL / IS NULL
        if "read_at is not null" in s:
            return getattr(obj, "read_at", None) is not None
        if "read_at is null" in s:
            return getattr(obj, "read_at", None) is None

        # resolved = true / false
        if "resolved = true" in s:
            return bool(getattr(obj, "resolved", False))
        if "resolved = false" in s:
            return not bool(getattr(obj, "resolved", False))

        return True

    @staticmethod
    def _extract_update_values(stmt: Any) -> dict[str, Any]:
        """从 Update 语句里提取 values 字典。"""
        try:
            return {k.key: v for k, v in stmt.values.items()}
        except Exception:
            return {}


class _MockRowcountResult:
    """模拟 SQLAlchemy Result 的 rowcount。"""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount

    def scalar_one(self) -> int:
        return self.rowcount

    def scalars(self) -> "_MockRowcountResult":
        return self

    def all(self) -> list[Any]:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_db() -> FakeDB:
    return FakeDB()


@pytest_asyncio.fixture
async def fake_session(fake_db: FakeDB) -> FakeAsyncSession:
    return FakeAsyncSession(fake_db)


# v0.7+：写操作路由强制 business_id（resolve_active_business 校验存在+active）。
# 每个测试默认 seed 一个 active 业务，请求体里用 ``_BIZ_ID`` 引用。
_BIZ_ID = uuid.uuid4()


def _mk_business(**kwargs: Any) -> Business:
    base = dict(
        id=_BIZ_ID,
        name="默认测试业务",
        slug="default-test-biz",
        description=None,
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        archived_at=None,
    )
    base.update(kwargs)
    return Business(**base)


@pytest_asyncio.fixture(autouse=True)
async def _seed_default_business(fake_db: FakeDB) -> None:
    fake_db.store[(Business, _BIZ_ID)] = _mk_business()


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
    return application


@pytest_asyncio.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# ---------------------------------------------------------------------------
# Mock LLM — 让 chat 路由走 FakeLLM，不发真实网络请求
# ---------------------------------------------------------------------------


class _FakeLLM:
    """最小的 LLMClient：返回固定 JSON。"""

    provider = "fake"

    def __init__(self, text: str = "{}") -> None:
        self._text = text

    async def complete(self, *args, **kwargs):
        from matrix.llm.clients import CompletionResult

        return CompletionResult(
            text=self._text,
            model="fake",
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
            provider=self.provider,
        )


@pytest.fixture
def mock_llm(monkeypatch):
    """把 chat 路由里的 get_default_client 换成假客户端。

    接受 ``text`` 参数指定返回文本；默认返回 ``theme_confirmed: true`` 的 JSON。
    """

    def _install(text: str) -> None:
        fake = _FakeLLM(text=text)
        from matrix.api.routes import chat as chat_mod
        from matrix.llm import router as router_mod

        monkeypatch.setattr(chat_mod, "get_default_client", lambda: fake)
        monkeypatch.setattr(router_mod, "get_default_client", lambda: fake)

    return _install


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert "version" in body
    assert "uptime_sec" in body
    assert body["db"] in ("ok", "error")
    assert body["tailscale"] in ("connected", "disconnected")


# ---------------------------------------------------------------------------
# /devices
# ---------------------------------------------------------------------------


def _mk_device(**kwargs: Any) -> Device:
    base = dict(
        id=uuid.uuid4(),
        nickname=kwargs.pop("nickname", "test-device"),
        model="Pixel 7",
        android_version="14",
        apk_version="0.1.0",
        tailnet_ip="100.64.0.1",
        tags=[],
        status="active",
        last_heartbeat=datetime.now(timezone.utc),
        adb_serial="ABC123",
        hmac_key_id=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        deleted_at=None,
    )
    base.update(kwargs)
    return Device(**base)


@pytest.mark.asyncio
async def test_list_devices_empty(client: AsyncClient) -> None:
    r = await client.get("/api/v1/devices")
    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.asyncio
async def test_register_device(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/devices",
        json={
            "nickname": "pixel-1",
            "model": "Pixel 7",
            "android_version": "14",
            "apk_version": "0.1.0",
            "tailnet_ip": "100.64.0.1",
            "adb_serial": "ABC123",
            "business_id": str(_BIZ_ID),
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["nickname"] == "pixel-1"
    assert body["status"] == "pending"
    assert "id" in body
    assert body["pair_code"].isdigit()
    assert len(body["pair_code"]) == 8


@pytest.mark.asyncio
async def test_get_device_not_found(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/devices/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_pair_device(client: AsyncClient, fake_session: FakeAsyncSession) -> None:
    d = _mk_device()
    fake_session.seed(d)
    registration = await client.post(
        "/api/v1/devices",
        json={
            "nickname": "pair-source",
            "model": "Pixel 7",
            "android_version": "14",
            "apk_version": "0.1.0",
            "tailnet_ip": "100.64.0.2",
            "business_id": str(_BIZ_ID),
        },
    )
    pair_source = registration.json()
    r = await client.post(
        f"/api/v1/devices/{pair_source['id']}/pair",
        json={"pair_code": pair_source["pair_code"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["key_id"].startswith("hmk_")
    assert "hmac_key" in body
    assert len(body["hmac_key"]) > 16  # base64 of 32B

    replay = await client.post(
        f"/api/v1/devices/{pair_source['id']}/pair",
        json={"pair_code": pair_source["pair_code"]},
    )
    assert replay.status_code == 400


@pytest.mark.asyncio
async def test_pair_device_bad_code(
    client: AsyncClient, fake_session: FakeAsyncSession
) -> None:
    d = _mk_device()
    fake_session.seed(d)
    r = await client.post(
        f"/api/v1/devices/{d.id}/pair",
        json={"pair_code": "000000"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_account(
    client: AsyncClient, fake_session: FakeAsyncSession
) -> None:
    device = _mk_device()
    fake_session.seed(device)
    r = await client.post(
        "/api/v1/accounts",
        json={
            "handle": "new_handle",
            "device_id": str(device.id),
            "business_id": str(_BIZ_ID),
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["handle"] == "new_handle"
    assert body["status"] == "pending"
    assert body["risk_score"] == 0


@pytest.mark.asyncio
async def test_get_account_not_found(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/accounts/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_notes_empty(client: AsyncClient) -> None:
    r = await client.get("/api/v1/notes")
    assert r.status_code == 200
    body = r.json()
    assert body == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_get_note_not_found(client: AsyncClient) -> None:
    r = await client.get(f"/api/v1/notes/{uuid.uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /goals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_goal(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/goals",
        json={"type": "publish_note", "target": {"theme": "测试主题", "count": 3}, "business_id": str(_BIZ_ID)},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["type"] == "publish_note"
    assert body["status"] == "active"
    assert body["target"] == {"theme": "测试主题", "count": 3}


@pytest.mark.asyncio
async def test_patch_goal_tuning_fields(client: AsyncClient) -> None:
    """v0.7 第 1 期：PATCH /goals/{id} 能改 max_rounds / target_likes / notes_per_round。"""
    # 先建一个 goal
    r = await client.post(
        "/api/v1/goals",
        json={
            "type": "publish_note",
            "target": {"theme": "测试"},
            "target_likes": 100,
            "notes_per_round": 3,
            "max_rounds": 2,
            "business_id": str(_BIZ_ID),
        },
    )
    assert r.status_code == 201
    goal_id = r.json()["id"]

    # 改 3 个字段
    r = await client.patch(
        f"/api/v1/goals/{goal_id}",
        json={"target_likes": 500, "notes_per_round": 5, "max_rounds": 4},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["target_likes"] == 500
    assert body["notes_per_round"] == 5
    assert body["max_rounds"] == 4

    # 部分更新：只改一个字段，其他不动
    r = await client.patch(
        f"/api/v1/goals/{goal_id}",
        json={"target_likes": 1000},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["target_likes"] == 1000
    assert body["notes_per_round"] == 5  # 没动
    assert body["max_rounds"] == 4  # 没动

    # 验证：notes_per_round 范围校验（>20 应 422）
    r = await client.patch(
        f"/api/v1/goals/{goal_id}",
        json={"notes_per_round": 100},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_goal_type_and_target(client: AsyncClient) -> None:
    """v0.7 B：PATCH /goals/{id} 能改 type 和 target（换方向继续）。"""
    r = await client.post(
        "/api/v1/goals",
        json={"type": "natural_language", "target": {"theme": "原主题"}, "business_id": str(_BIZ_ID)},
    )
    assert r.status_code == 201
    goal_id = r.json()["id"]

    # 改 type + target
    r = await client.patch(
        f"/api/v1/goals/{goal_id}",
        json={
            "type": "publish_note",
            "target": {"theme": "新主题", "audience": "20-30岁"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "publish_note"
    assert body["target"]["theme"] == "新主题"
    assert body["target"]["audience"] == "20-30岁"

    # 验证：非法 type 422
    r = await client.patch(
        f"/api/v1/goals/{goal_id}",
        json={"type": "not_a_real_type"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_goal_hard_delete(client: AsyncClient) -> None:
    """v0.7：DELETE /goals/{id} 物理删。删后 GET 404，list 不再返回。"""
    r = await client.post(
        "/api/v1/goals",
        json={"type": "natural_language", "target": {"theme": "删我"}, "business_id": str(_BIZ_ID)},
    )
    assert r.status_code == 201
    goal_id = r.json()["id"]

    # 删
    r = await client.delete(f"/api/v1/goals/{goal_id}")
    assert r.status_code == 204

    # 删了后 GET 返 404
    r = await client.get(f"/api/v1/goals/{goal_id}")
    assert r.status_code == 404

    # list 不再返回
    r = await client.get("/api/v1/goals")
    assert goal_id not in [g["id"] for g in r.json()["items"]]

    # 删两次也是 404（幂等）
    r = await client.delete(f"/api/v1/goals/{goal_id}")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_goal_status_cancelled(client: AsyncClient) -> None:
    """v0.7 B：PATCH /goals/{id} 把 status 改成 cancelled，手动停 goal。"""
    r = await client.post(
        "/api/v1/goals",
        json={"type": "publish_note", "target": {"theme": "x"}, "business_id": str(_BIZ_ID)},
    )
    assert r.status_code == 201
    goal_id = r.json()["id"]
    assert r.json()["status"] == "active"

    # 改成 cancelled
    r = await client.patch(
        f"/api/v1/goals/{goal_id}",
        json={"status": "cancelled"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    # 验证：再 GET 仍是 cancelled
    r = await client.get(f"/api/v1/goals/{goal_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    # 验证：非法 status 422
    r = await client.patch(
        f"/api/v1/goals/{goal_id}",
        json={"status": "frozen"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /agent/runs
# ---------------------------------------------------------------------------


def _mk_run(**kwargs: Any) -> AgentRun:
    now = datetime.now(timezone.utc)
    base = dict(
        id=uuid.uuid4(),
        goal_id=kwargs.pop("goal_id", uuid.uuid4()),
        current_state="IDLE",
        checkpoint=None,
        payload={},
        status="running",
        started_at=now,
        updated_at=now,
        ended_at=None,
    )
    base.update(kwargs)
    return AgentRun(**base)


@pytest.mark.asyncio
async def test_list_agent_runs_empty(client: AsyncClient) -> None:
    r = await client.get("/api/v1/agent/runs")
    assert r.status_code == 200
    assert r.json() == {"items": []}


@pytest.mark.asyncio
async def test_cancel_agent_run(
    client: AsyncClient, fake_session: FakeAsyncSession
) -> None:
    run = _mk_run()
    fake_session.seed(run)
    r = await client.post(f"/api/v1/agent/runs/{run.id}/cancel")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_cancel_agent_run_not_found(client: AsyncClient) -> None:
    r = await client.post(f"/api/v1/agent/runs/{uuid.uuid4()}/cancel")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_unknown_intent_for_create_goal(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """v0.7+：chat 不再支持建目标。LLM 输出 create_goal intent 应被识别为 unknown_intent
    并引导用户去 /goals 手动表单。"""
    mock_llm(
        '{"reply": "建目标请去 /goals 页面", '
        '"intent": "create_goal", '
        '"args": {}}'
    )
    r = await client.post("/api/v1/chat", json={"message": "建一个夏季女鞋 goal", "business_id": str(_BIZ_ID)})
    assert r.status_code == 200
    body = r.json()
    assert "reply" in body
    assert body["action"]["type"] == "unknown_intent"
    assert body["action"]["payload"]["raw_intent"] == "create_goal"
    assert "/goals" in body["action"]["payload"]["raw_intent"] or "建目标" in body["reply"]


@pytest.mark.asyncio
async def test_chat_pause_no_longer_keyword_short_circuit(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """v0.7+：暂停不再是关键词短路（chat.py 删了 _PAUSE_PATTERNS）。
    LLM 必须先输出 intent（让 chat_tools 走 preview_change 路径）才执行写操作。"""
    # LLM 返回 chitchat（"暂停" 不是特殊 token）→ 走 chitchat，不写库
    mock_llm('{"reply": "你是要暂停某个 goal 吗？请告诉我具体是哪个。", "intent": "chitchat", "args": {}}')
    seeded_run = _mk_run(status="running")
    fake_session.seed(seeded_run)
    await fake_session.flush()
    r = await client.post("/api/v1/chat", json={"message": "暂停", "business_id": str(_BIZ_ID)})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "chitchat"
    # 验证 DB 没被改：seeded 的 run 仍在 store 里且 status=running
    stored = fake_session._db.store.get((AgentRun, seeded_run.id))
    assert stored is not None
    assert stored.status == "running"


@pytest.mark.asyncio
async def test_chat_empty_message(client: AsyncClient) -> None:
    r = await client.post("/api/v1/chat", json={"message": "", "business_id": str(_BIZ_ID)})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "noop"


# ---------------------------------------------------------------------------
# /metrics/summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_summary(client: AsyncClient) -> None:
    r = await client.get("/api/v1/metrics/summary")
    assert r.status_code == 200
    body = r.json()
    assert "devices" in body
    assert "accounts" in body
    assert "tasks" in body
    assert body["devices"]["total"] == 0
    assert body["accounts"]["total"] == 0
    assert body["tasks"]["pending"] == 0


# ---------------------------------------------------------------------------
# 错误码 envelope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_error_envelope(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/devices",
        json={"nickname": "x"},  # 缺必填字段
    )
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["retryable"] is False
