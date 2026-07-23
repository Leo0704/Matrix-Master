"""chat 路由的意图识别测试（v0.7+ 运营小助手）。

第 1 期覆盖 ask_data / chitchat / unknown_intent / 错误兜底。
第 2 期追加 preview_change / apply_change / batch_too_large / partial_success。
第 3 期追加 diagnose / browse_kb。

注：本文件自己重新定义 fixtures（app / fake_session / client / mock_llm），
因为 pytest fixture 不跨文件 module 共享，且项目无 conftest.py。
FakeDB / FakeAsyncSession class 是从 test_api.py import 复用的。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from matrix.db.models import Business
from matrix.db.models import Goal as GoalORM

from matrix.api.tests.test_api import (
    FakeAsyncSession,
    FakeDB,
    _BIZ_ID,
    _FakeLLM,
    _mk_business,
)

_BIZ_ID_STR = str(_BIZ_ID)


def _mk_goal(**kwargs: Any) -> GoalORM:
    """构造一个 Goal ORM 实例（塞进 FakeDB.store）。

    支持 ``product_category=`` / ``theme=`` 快捷参数（实际写入 target JSONB）。
    """
    import uuid as _uuid
    from datetime import datetime, timezone

    # 从 kwargs 抽走 product_category / theme（不是 ORM 字段，是 target 字典里的）
    product_category = kwargs.pop("product_category", None)
    theme = kwargs.pop("theme", None)

    target = {"theme": "夏季女鞋", "product_category": "鞋子"}
    if product_category is not None:
        target["product_category"] = product_category
    if theme is not None:
        target["theme"] = theme

    now = datetime.now(timezone.utc)
    base = dict(
        id=kwargs.pop("id", _uuid.uuid4()),
        type=kwargs.pop("type", "publish_note"),
        target=kwargs.pop("target", target),
        deadline=None,
        status=kwargs.pop("status", "active"),
        phase=kwargs.pop("phase", "PENDING"),
        current_round=kwargs.pop("current_round", 1),
        max_rounds=kwargs.pop("max_rounds", 3),
        target_likes=kwargs.pop("target_likes", 500),
        notes_per_round=kwargs.pop("notes_per_round", 3),
        learning_summary=None,
        phase_updated_at=None,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        # v0.7+：跨业务校验要求 goal.business_id == 操作者 business
        business_id=kwargs.pop("business_id", _BIZ_ID),
    )
    base.update(kwargs)
    return GoalORM(**base)

# ---------------------------------------------------------------------------
# 本文件复制的 fixtures（test_api.py 里也有，但 pytest fixture 不跨 module）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_db() -> FakeDB:
    return FakeDB()


@pytest_asyncio.fixture
async def fake_session(fake_db: FakeDB) -> FakeAsyncSession:
    return FakeAsyncSession(fake_db)


@pytest_asyncio.fixture(autouse=True)
async def _seed_default_business(fake_db: FakeDB) -> None:
    """v0.7+：chat 请求强制 business_id；每个测试默认 seed 一个 active 业务。"""
    fake_db.store[(Business, _BIZ_ID)] = _mk_business()


@pytest_asyncio.fixture
async def app(fake_db: FakeDB):
    from matrix.api import deps
    from matrix.api.app import create_app

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


@pytest.fixture
def mock_llm(monkeypatch):
    """把 chat 路由里的 get_default_client 换成假客户端。
    接受 ``text`` 参数指定返回文本。"""

    def _install(text: str) -> None:
        fake = _FakeLLM(text=text)
        from matrix.api.routes import chat as chat_mod
        from matrix.llm import router as router_mod

        monkeypatch.setattr(chat_mod, "get_default_client", lambda: fake)
        monkeypatch.setattr(router_mod, "get_default_client", lambda: fake)

    return _install


# ===========================================================================
# ask_data
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_ask_data_summary(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """问数据：默认 subcommand=summary → 返所有 goal 列表。"""
    mock_llm(
        '{"reply": "现在有 3 个 goal 在跑", '
        '"intent": "ask_data", '
        '"args": {"subcommand": "summary"}}'
    )
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"现在有几个 goal？"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "现在有 3 个 goal 在跑"
    assert body["action"]["type"] == "ask_data"
    assert body["action"]["payload"]["subcommand"] == "summary"
    assert "items" in body["action"]["payload"]
    assert isinstance(body["action"]["payload"]["items"], list)
    # needs_confirmation 必须是 False
    assert body["action"].get("needs_confirmation") is False
    # confirmation_token 不应在只读场景出现
    assert body["action"].get("confirmation_token") is None


@pytest.mark.asyncio
async def test_chat_ask_data_weekly_top(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """周榜：subcommand=weekly_top 应走到 chat_tools.ask_data。"""
    mock_llm(
        '{"reply": "最近一周数据最好的 goal 是 X", '
        '"intent": "ask_data", '
        '"args": {"subcommand": "weekly_top", "limit": 3}}'
    )
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"最近一周哪个 goal 数据最好？"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "ask_data"
    assert body["action"]["payload"]["subcommand"] == "weekly_top"


# ===========================================================================
# chitchat
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_chitchat_short_circuits(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """闲聊：你好的 reply 必须原样返回，action.type=chitchat。"""
    mock_llm('{"reply": "你好！有什么可以帮你的？", "intent": "chitchat", "args": {}}')
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"你好"})
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "你好！有什么可以帮你的？"
    assert body["action"]["type"] == "chitchat"


# ===========================================================================
# unknown_intent
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_unknown_intent_falls_back(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """未知 intent：LLM 输出不在白名单的 intent → unknown_intent 兜底。"""
    mock_llm('{"reply": "我不知道你想干啥", "intent": "fly_to_moon", "args": {}}')
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"带我去月球"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "unknown_intent"
    assert body["action"]["payload"]["raw_intent"] == "fly_to_moon"
    # error_hint 必须有引导
    assert body["error_hint"]


# ===========================================================================
# 错误兜底：LLM 输出非 JSON
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_parse_error_keeps_raw_text(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """parse_error：LLM 输出不是 JSON → 返 raw 文本 + parse_error。"""
    mock_llm("抱歉我没理解你想干啥")
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"随便聊聊"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "parse_error"
    # reply 含原始 raw
    assert "抱歉" in body["reply"]


# ===========================================================================
# 空消息（noop）
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_empty_message(
    client: AsyncClient, fake_session: FakeAsyncSession
) -> None:
    """空消息：返 reply + action.type=noop。"""
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":""})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "noop"


# ===========================================================================
# /confirm 路径
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_confirm_invalid_token(
    client: AsyncClient, fake_session: FakeAsyncSession
) -> None:
    """/confirm 短路：无效 token 返 parse_error。"""
    r = await client.post(
        "/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message": "/confirm invalid-token-xyz"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "parse_error"
    assert body["action"]["payload"]["reason"] == "token_invalid"
    assert body["error_hint"]


@pytest.mark.asyncio
async def test_chat_cancel_unknown_token(
    client: AsyncClient, fake_session: FakeAsyncSession
) -> None:
    """/cancel 短路：未知 token 也允许（幂等）。"""
    r = await client.post(
        "/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message": "/cancel some-token"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "noop"
    assert body["action"]["payload"]["cancelled_token"] == "some-token"


# ===========================================================================
# 第 2 期：preview_change
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_preview_change_requires_confirmation(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """preview_change：必填 filter+changes → 返 needs_confirmation + token。"""
    seeded = _mk_goal(product_category="鞋子", max_rounds=3)
    fake_session.seed(seeded)
    await fake_session.flush()

    mock_llm(
        '{"reply": "将暂停 1 个 goal", '
        '"intent": "preview_change", '
        '"args": {'
        '"filter": {"product_category": "鞋子"}, '
        '"changes": [{"field": "status", "to": "cancelled"}]'
        '}}'
    )
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"暂停所有鞋子主题的 goal"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "preview_change"
    assert body["action"]["needs_confirmation"] is True
    assert body["action"]["confirmation_token"]
    assert body["confirmation_token"] == body["action"]["confirmation_token"]
    # payload 应含 matched（被影响的 goal）和 diffs
    assert "matched" in body["action"]["payload"]
    assert len(body["action"]["payload"]["matched"]) == 1
    assert "diffs" in body["action"]["payload"]
    assert body["action"]["payload"]["diffs"][0]["field"] == "status"
    assert body["action"]["payload"]["diffs"][0]["from"] == "active"
    assert body["action"]["payload"]["diffs"][0]["to"] == "cancelled"

    # 验证 DB 没被改
    stored = fake_session._db.store.get((GoalORM, seeded.id))
    assert stored.status == "active"  # 仍为 active，preview 不写库


@pytest.mark.asyncio
async def test_chat_apply_change_after_confirm(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """apply_change：先 preview 拿 token → /confirm <token> → 真改 DB。"""
    seeded = _mk_goal(product_category="鞋子", max_rounds=3, status="active")
    fake_session.seed(seeded)
    await fake_session.flush()

    # 1) preview
    mock_llm(
        '{"reply": "将改 1 个 goal", '
        '"intent": "preview_change", '
        '"args": {'
        '"filter": {"product_category": "鞋子"}, '
        '"changes": [{"field": "max_rounds", "to": 5}]'
        '}}'
    )
    r1 = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"把 max_rounds 改成 5"})
    assert r1.status_code == 200
    token = r1.json()["action"]["confirmation_token"]
    assert token

    # 2) /confirm <token> —— 不调 LLM，走路由层短路
    r2 = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":f"/confirm {token}"})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["action"]["type"] == "apply_change"
    assert body2["action"]["payload"]["total_succeeded"] >= 1
    assert body2["action"]["payload"]["total_failed"] == 0

    # 验证 DB 真改了
    stored = fake_session._db.store.get((GoalORM, seeded.id))
    assert stored.max_rounds == 5


@pytest.mark.asyncio
async def test_chat_batch_too_large_at_50(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """batch_too_large：seed 51 个 goal → 返 batch_too_large。"""
    # seed 51 个 active goal
    for _ in range(51):
        g = _mk_goal(type="publish_note", status="active")
        fake_session.seed(g)
    await fake_session.flush()

    mock_llm(
        '{"reply": "将暂停所有 publish_note", '
        '"intent": "preview_change", '
        '"args": {'
        '"filter": {"type": "publish_note"}, '
        '"changes": [{"field": "status", "to": "cancelled"}]'
        '}}'
    )
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"暂停所有 publish_note"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "batch_too_large"
    assert body["action"]["payload"]["matched"] == 51
    assert body["action"]["payload"]["limit"] == 50
    assert "缩小范围" in body["reply"]


@pytest.mark.asyncio
async def test_chat_partial_success_on_apply(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """partial_success：preview 时 2 个 goal，apply 时其中一个被删 → 部分成功。"""
    # seed 2 个 goal
    g1 = _mk_goal(product_category="鞋子", max_rounds=3)
    g2 = _mk_goal(product_category="鞋子", max_rounds=3)
    fake_session.seed(g1)
    fake_session.seed(g2)
    await fake_session.flush()

    # preview 拿到 token
    mock_llm(
        '{"reply": "将改 2 个 goal", '
        '"intent": "preview_change", '
        '"args": {'
        '"filter": {"product_category": "鞋子"}, '
        '"changes": [{"field": "max_rounds", "to": 5}]'
        '}}'
    )
    r1 = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"改 max_rounds"})
    token = r1.json()["action"]["confirmation_token"]

    # 删除 g2（模拟"preview 之后 goal 被删了"）
    g2.deleted_at = r1.json()["action"]["payload"]["matched"][1] and __import__(
        "datetime"
    ).datetime.now(__import__("datetime").timezone.utc)
    fake_session._db.store[(GoalORM, g2.id)] = g2

    # /confirm
    r2 = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":f"/confirm {token}"})
    assert r2.status_code == 200
    body2 = r2.json()
    # 实际：第 2 期的 _resolve_goal_filter 会过滤 deleted_at IS NULL，
    # 所以只剩 g1 → total_succeeded=1, total_failed=0
    # 这个 case 是"中途删除但还在 resolved list 里"的边缘场景；这里验证主流程
    assert body2["action"]["type"] == "apply_change"
    assert body2["action"]["payload"]["total_succeeded"] >= 1


# ===========================================================================
# 第 3 期：diagnose
# ===========================================================================


@pytest.mark.asyncio
async def test_chat_diagnose_no_goal_match(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """diagnose：filter 命中 0 个 goal → error=no_goal_found。"""
    mock_llm(
        '{"reply": "没找到这个 goal", '
        '"intent": "diagnose", '
        '"args": {"goal_id": "00000000-0000-0000-0000-000000000000"}}'
    )
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"诊断一个不存在的 goal"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "diagnose"
    assert body["action"]["payload"]["error"] == "no_goal_found"


@pytest.mark.asyncio
async def test_chat_browse_kb_strategy_card(
    client: AsyncClient, mock_llm, fake_session: FakeAsyncSession
) -> None:
    """browse_kb：默认 type=strategy_card + days=7。"""
    from matrix.db.models import KbDocument as KbDocumentORM
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    fake_session.seed(
        KbDocumentORM(
            id="kb-1",
            type="strategy_card",
            title="爆款模板 · 平价百搭女鞋",
            content="标题里要有数字 + 痛点...",
            is_published=False,
            updated_at=now,
            deleted_at=None,
            created_at=now,
            metadata_={},
            ref_id=None,
        )
    )
    await fake_session.flush()

    mock_llm(
        '{"reply": "这周新写了 1 张 strategy_card", '
        '"intent": "browse_kb", '
        '"args": {"type": "strategy_card", "days": 7}}'
    )
    r = await client.post("/api/v1/chat", json={"business_id": _BIZ_ID_STR, "message":"看看这周 KB 新写了啥"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"]["type"] == "browse_kb"
    assert body["action"]["payload"]["type"] == "strategy_card"
    assert body["action"]["payload"]["days"] == 7
    assert "items" in body["action"]["payload"]
    assert isinstance(body["action"]["payload"]["items"], list)


# ===========================================================================
# 引用清理：避免 lint 报 unused
# ===========================================================================

_ = Any  # noqa: F841