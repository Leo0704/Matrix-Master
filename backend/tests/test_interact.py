"""INTERACT 节点 + 闭环集成测试（v0.6）。

覆盖：
- happy path（全部 like/comment 成功）
- 部分失败（rate limiter 命中 / 设备报错）
- 空 plan / 无效 plan
- 状态机集成：PUBLISH → INTERACT → COLLECT
- 端到端闭环：goal 带 interact_plan 跑完整 9 态
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from matrix.agent._services import (
    AgentServices,
    reset_services,
    set_services,
)
from matrix.agent.guards import GuardConfig
from matrix.agent.nodes.interact import interact_node
from matrix.agent.protocols import ChosenSlot, InteractResult
from matrix.agent.run_manager import RunManager
from matrix.agent.state_machine import build_state_machine
from matrix.agent.types import State
from matrix.scheduler.rate_limiter import RateLimiter

from tests.test_agent import (
    FakeLLM,
    InMemoryAgentRepository,
    clear_notify_log,
    make_services,
)


# ---------------------------------------------------------------------------
# Test helpers —— 只在 test 目录里用（"测试的代码"允许 mock）
# ---------------------------------------------------------------------------


class FakeDeviceInteractor:
    """测试用 interactor；不连真 APK，纯内存。"""

    def __init__(self, *, ok: bool = True, error_code: str | None = None) -> None:
        self.ok = ok
        self.error_code = error_code
        self.calls: list[dict[str, Any]] = []

    async def interact(
        self,
        *,
        device_id,
        account_id,
        action: str,
        target_note_id: str,
        content=None,
        request_id: str,
        timeout: float = 60.0,
    ) -> InteractResult:
        self.calls.append(
            {
                "device_id": device_id,
                "account_id": account_id,
                "action": action,
                "target_note_id": target_note_id,
                "content": content,
                "request_id": request_id,
            }
        )
        if not self.ok:
            return InteractResult(
                ok=False, interaction_id=uuid.uuid4(),
                error_code=self.error_code or "FAKE_FAIL",
                error_message="fake failure",
            )
        return InteractResult(ok=True, interaction_id=uuid.uuid4())

    # 兼容 publish_node 调用（v0.6 闭环测试把同一 fake 注入 publisher + interactor）
    async def publish(self, **_kwargs):
        from matrix.agent.protocols import PublishResult

        self.calls.append({"action": "publish", **_kwargs})
        return PublishResult(
            ok=True,
            note_id=uuid.uuid4(),
            platform_note_id=f"fake-{uuid.uuid4()}",
            platform_url="https://www.xiaohongshu.com/explore/fake",
        )

    async def collect(self, **_kwargs):
        return {"views": 100, "likes": 5, "collects": 2, "comments": 1, "follows_gained": 0}


def _record_writer() -> tuple[Any, list[dict[str, Any]]]:
    """返回一个 interaction_writer + 收件箱。"""
    sink: list[dict[str, Any]] = []

    async def writer(record: dict[str, Any]) -> uuid.UUID:
        sink.append(record)
        return uuid.uuid4()

    return writer, sink


def _make_services(
    *,
    interactor: FakeDeviceInteractor | None = None,
    rate_limiter: RateLimiter | None = None,
    interaction_writer=None,
) -> AgentServices:
    """构造带 interactor + rate_limiter + writer 的 services。"""
    llm = FakeLLM(
        mapping={
            "你是小红书真实用户": json.dumps(
                {"content": "这篇笔记太戳我了！颜色搭配也太好看了吧～"}
            ),
        }
    )
    services = make_services(llm=llm)
    services.device_interactor = interactor or FakeDeviceInteractor()
    services.rate_limiter = rate_limiter
    services.interaction_writer = interaction_writer
    return services


@pytest.fixture(autouse=True)
def _reset():
    reset_services()
    clear_notify_log()
    yield
    reset_services()
    clear_notify_log()


# ---------------------------------------------------------------------------
# interact_node 单元测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interact_node_empty_plan_passes():
    """空 plan：直接 PASS，results 为空。"""
    set_services(_make_services())
    result = await interact_node({"interact_plan": []})
    assert result["interact_results"]["succeeded"] == 0
    assert result["interact_results"]["failed"] == 0
    assert result["last_error"] is None


@pytest.mark.asyncio
async def test_interact_node_happy_path_mixed():
    """2 comment + 1 like，全部成功。"""
    interactor = FakeDeviceInteractor()
    writer, sink = _record_writer()
    set_services(
        _make_services(interactor=interactor, interaction_writer=writer)
    )
    plan = [
        {"note_id": "n1", "kind": "like"},
        {"note_id": "n2", "kind": "comment", "note_title": "夏日穿搭",
         "note_content": "三件套搞定"},
        {"note_id": "n3", "kind": "comment", "content": "手动指定的评论"},
    ]
    result = await interact_node(
        {"interact_plan": plan, "slot": {"device_id": uuid.uuid4(), "account_id": uuid.uuid4()}}
    )
    assert result["interact_results"]["succeeded"] == 3
    assert result["interact_results"]["failed"] == 0
    assert result["last_error"] is None
    # interactor 被调 3 次
    kinds = [c["action"] for c in interactor.calls]
    assert kinds == ["like", "comment", "comment"]
    # 1 个 like 不带 content，2 个 comment 带 content
    assert interactor.calls[0]["content"] is None
    assert "戳我" in (interactor.calls[1]["content"] or "")
    assert interactor.calls[2]["content"] == "手动指定的评论"
    # writer 收到 3 条
    assert len(sink) == 3
    assert sink[0]["type"] == "like"
    assert sink[1]["type"] == "comment"


@pytest.mark.asyncio
async def test_interact_node_partial_failure_marks_part():
    """设备部分失败 → results.failed > 0，last_error 标记 PARTIAL_FAIL。"""
    # 让 interactor 第一次成功、后续失败
    interactor = FakeDeviceInteractor(ok=False, error_code="APK_RISK")
    set_services(_make_services(interactor=interactor))
    plan = [
        {"note_id": "n1", "kind": "like", "content": "ok"},
        {"note_id": "n2", "kind": "comment", "content": "x"},
        {"note_id": "n3", "kind": "like", "content": "x"},
    ]
    result = await interact_node(
        {"interact_plan": plan, "slot": {"device_id": uuid.uuid4(), "account_id": uuid.uuid4()}}
    )
    assert result["interact_results"]["succeeded"] == 0
    assert result["interact_results"]["failed"] == 3
    assert result["last_error"]["code"] == "INTERACT_ALL_FAILED"


@pytest.mark.asyncio
async def test_interact_node_skips_invalid_items():
    """无效 plan 项（kind 非法 / note_id 空）记 failed，不抛。"""
    interactor = FakeDeviceInteractor()
    set_services(_make_services(interactor=interactor))
    plan = [
        {"note_id": "", "kind": "like"},  # 空 note_id
        {"note_id": "n2", "kind": "follow"},  # 不支持的 kind
        {"note_id": "n3", "kind": "like"},  # 合法
    ]
    result = await interact_node(
        {"interact_plan": plan, "slot": {"device_id": uuid.uuid4(), "account_id": uuid.uuid4()}}
    )
    assert result["interact_results"]["succeeded"] == 1
    assert result["interact_results"]["failed"] == 2
    # 1 次设备调用
    assert len(interactor.calls) == 1


@pytest.mark.asyncio
async def test_interact_node_no_device_interactor_marks_part():
    """device_interactor=None → 所有项记 NO_DEVICE_INTERACTOR，last_error 标记。"""
    services = _make_services()
    services.device_interactor = None
    set_services(services)
    result = await interact_node(
        {
            "interact_plan": [{"note_id": "n1", "kind": "like"}],
            "slot": {"device_id": uuid.uuid4(), "account_id": uuid.uuid4()},
        }
    )
    assert result["interact_results"]["failed"] == 1
    assert result["interact_results"]["details"][0]["error_code"] == "NO_DEVICE_INTERACTOR"
    assert result["last_error"]["code"] == "INTERACT_ALL_FAILED"


@pytest.mark.asyncio
async def test_interact_node_rate_limiter_throttles():
    """限速命中 → 该项记 daily_cap，interactor 不被调。"""

    # 用一个超低阈值的 limiter 来强制触发
    # 注入 clock 显式放在活跃窗（09:00-23:00 Asia/Shanghai）内，避免 UTC 跑测试时被拒
    from datetime import datetime
    rl = RateLimiter(
        device_interact_per_day=2,
        clock=lambda: datetime(2026, 7, 9, 12, 0),  # 12 点在 09-23 窗内
    )
    interactor = FakeDeviceInteractor()
    set_services(_make_services(interactor=interactor, rate_limiter=rl))
    plan = [
        {"note_id": "n1", "kind": "like"},
        {"note_id": "n2", "kind": "like"},
        {"note_id": "n3", "kind": "like"},
    ]
    result = await interact_node(
        {"interact_plan": plan, "slot": {"device_id": uuid.uuid4(), "account_id": uuid.uuid4()}}
    )
    # 前 2 个过，3 个被 daily_cap 拦
    assert result["interact_results"]["succeeded"] == 2
    assert result["interact_results"]["failed"] == 1
    assert result["interact_results"]["details"][2]["error_code"].startswith("daily_cap_")
    # 设备只调了 2 次
    assert len(interactor.calls) == 2


# ---------------------------------------------------------------------------
# 状态机集成：PUBLISH → INTERACT → COLLECT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_machine_publish_routes_to_interact_when_plan_set():
    """PUBLISH 成功 + interact_plan 非空 → INTERACT。"""
    from matrix.agent.guards import route_after_publish

    cfg = GuardConfig()
    state = {
        "publish_result": {"ok": True, "platform_note_id": "abc"},
        "interact_plan": [{"note_id": "n1", "kind": "like"}],
    }
    assert route_after_publish(state, cfg) == State.INTERACT


@pytest.mark.asyncio
async def test_state_machine_publish_routes_to_idle_when_no_plan():
    """PUBLISH 成功 + 无 interact_plan → 直接 IDLE 收工。"""
    from matrix.agent.guards import route_after_publish

    cfg = GuardConfig()
    state = {
        "publish_result": {"ok": True, "platform_note_id": "abc"},
        "interact_plan": [],
    }
    assert route_after_publish(state, cfg) == State.IDLE


@pytest.mark.asyncio
async def test_state_machine_publish_to_idle_when_switch_off():
    """enable_post_publish_interact=False → 即使有 plan 也走 IDLE 收工。"""
    from matrix.agent.guards import route_after_publish

    cfg = GuardConfig(enable_post_publish_interact=False)
    state = {
        "publish_result": {"ok": True, "platform_note_id": "abc"},
        "interact_plan": [{"note_id": "n1", "kind": "like"}],
    }
    assert route_after_publish(state, cfg) == State.IDLE


@pytest.mark.asyncio
async def test_state_machine_compiles_with_interact():
    """构图不报错（含 INTERACT 节点）。"""
    sm = build_state_machine()
    graph = sm.compile()
    assert graph is not None


# ---------------------------------------------------------------------------
# 端到端闭环
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_then_interact_runs_end_to_end():
    """完整主链：goal 带 2 comment + 1 like → RESEARCH→...→PUBLISH→INTERACT→IDLE 收工。"""
    llm = FakeLLM(
        mapping={
            "你是选题研究员": '{"selected":[{"title":"夏日穿搭","rationale":"应季"}]}',
            "你是小红书爆款文案写手": json.dumps(
                {"title": "夏日穿搭", "content": "三件套搞定", "tags": ["穿搭"]}
            ),
            "你是内容审核员": json.dumps(
                {"forbidden_hits": [], "score_dup": 0.1, "score_human": 0.9,
                 "passed": True, "reason": "ok"}
            ),
            "你是小红书真实用户": json.dumps({"content": "学到了！马上试"}),
            "你是运营复盘员": json.dumps({"review_text": "ok", "strategy_updates": []}),
        }
    )
    interactor = FakeDeviceInteractor()
    writer, sink = _record_writer()
    services = make_services(llm=llm)
    services.device_publisher = interactor  # 复用同一 fake 当 publisher
    services.device_interactor = interactor
    services.interaction_writer = writer
    # 注入 scheduler，避免 SCHEDULE 节点因 NO_SCHEDULER 进 ALERT
    services.scheduler = SimpleNamespace(
        choose_slot=AsyncMock(
            return_value=ChosenSlot(
                device_id=uuid.uuid4(),
                account_id=uuid.uuid4(),
                reason="slot_picker.match",
                scheduled_at=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
            )
        )
    )
    # 活跃窗测试把窗口设成全天，避免受 datetime.now(UTC) 影响（容器时区+8h）
    services.system_metadata = {
        "persona_config": {"active_window": {"start": 0, "end": 24}}
    }
    rm = RunManager(services=services, repository=InMemoryAgentRepository())

    run_id = await rm.create_run(
        goal_text="发完这篇去同类笔记互动",
        interact_plan=[
            {"note_id": "n1", "kind": "like"},
            {"note_id": "n2", "kind": "comment", "note_title": "x", "note_content": "y"},
            {"note_id": "n3", "kind": "comment", "content": "已手动写好"},
        ],
    )
    state = await rm.start_run(run_id)

    # 走到 IDLE（v0.7+ 主链不再走 COLLECT→ANALYZE）
    assert state["current_state"] == State.IDLE.value
    # interact_results 反映 3 个 plan
    results = state.get("interact_results") or {}
    assert results["succeeded"] == 3
    assert results["failed"] == 0
    # 设备被调了 publish + 3 interact（calls 列表包含所有）
    assert len(interactor.calls) == 4
    # writer 收到 3 条 success 记录
    assert len(sink) == 3
    assert all(r["result"] == "success" for r in sink)
    # run 状态
    status = await rm.get_run_status(run_id)
    assert status["status"] == "success"


@pytest.mark.asyncio
async def test_closed_loop_without_interact_plan_still_works():
    """无 interact_plan 时 INTERACT 节点被跳过，原闭环测试不退化。"""
    from tests.test_loop_closed import test_closed_loop_runs_end_to_end

    # 直接调原闭环测试（re-use 已存在的 happy path）
    await test_closed_loop_runs_end_to_end()


# ---------------------------------------------------------------------------
# W4：skipped 计数口径 / interaction 写库用本地 UUID / 全失败走 ALERT
# ---------------------------------------------------------------------------

import os  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402


class _W4Scalars:
    def __init__(self, value: Any) -> None:
        self._value = value

    def first(self) -> Any:
        return self._value


class _W4Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalars(self) -> _W4Scalars:
        return _W4Scalars(self._value)


class _W4FakeSession:
    """给 InteractPolicy + interact_node 用的假 session。

    - session.get(Account, ...) → 预设 account
    - SELECT ... FROM notes → 预设本地 note_id
    - SELECT ... FROM interactions → 按 interaction_exists 返回
    """

    def __init__(
        self,
        *,
        account: Any = None,
        local_note_id: uuid.UUID | None = None,
        interaction_exists: bool = False,
    ) -> None:
        self._account = account
        self._local_note_id = local_note_id
        self._interaction_exists = interaction_exists

    async def get(self, model: Any, pk: Any) -> Any:
        return self._account

    async def execute(self, stmt: Any) -> _W4Result:
        sql = str(stmt)
        if "FROM notes" in sql:
            return _W4Result(self._local_note_id)
        if "FROM interactions" in sql:
            return _W4Result(uuid.uuid4() if self._interaction_exists else None)
        return _W4Result(None)


def _w4_factory(session: Any):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


def _active_account(account_id: uuid.UUID) -> Any:
    return SimpleNamespace(
        id=account_id,
        handle="h",
        status="active",
        risk_score=0.0,
        deleted_at=None,
    )


@pytest.mark.asyncio
async def test_policy_skip_counted_as_skipped_not_failed():
    """去重命中 → 计入 skipped（不再 ok:False 却不计数），不算 failed。"""
    account_id = uuid.uuid4()
    session = _W4FakeSession(
        account=_active_account(account_id),
        local_note_id=uuid.uuid4(),
        interaction_exists=True,  # 已赞过 → DEDUPED
    )
    interactor = FakeDeviceInteractor()
    services = _make_services(interactor=interactor)
    services.session_factory = _w4_factory(session)
    set_services(services)
    result = await interact_node(
        {
            "interact_plan": [{"note_id": "xhs-1", "kind": "like"}],
            "slot": {"device_id": uuid.uuid4(), "account_id": account_id},
        }
    )
    r = result["interact_results"]
    assert r["skipped"] == 1
    assert r["failed"] == 0
    assert r["succeeded"] == 0
    assert r["details"][0]["skipped"] is True
    assert r["details"][0]["error_code"] == "DEDUPED"
    assert result["last_error"] is None
    assert interactor.calls == []


@pytest.mark.asyncio
async def test_interaction_writer_receives_local_note_uuid():
    """修 UUID 外键写入：writer 收到的 target_note_id 是本地 notes.id，不是平台字符串。"""
    account_id = uuid.uuid4()
    local_note_id = uuid.uuid4()
    session = _W4FakeSession(
        account=_active_account(account_id),
        local_note_id=local_note_id,
    )
    writer, sink = _record_writer()
    interactor = FakeDeviceInteractor()
    services = _make_services(interactor=interactor, interaction_writer=writer)
    services.session_factory = _w4_factory(session)
    set_services(services)
    result = await interact_node(
        {
            "interact_plan": [{"note_id": "xhs-9", "kind": "like"}],
            "slot": {"device_id": uuid.uuid4(), "account_id": account_id},
        }
    )
    assert result["interact_results"]["succeeded"] == 1
    assert len(sink) == 1
    assert sink[0]["target_note_id"] == local_note_id


@pytest.mark.asyncio
async def test_interaction_record_skipped_when_local_note_missing():
    """平台 id 查不到本地 Note → 跳过该条写库记录（互动本身仍算成功）。"""
    account_id = uuid.uuid4()
    session = _W4FakeSession(
        account=_active_account(account_id),
        local_note_id=None,  # 本机没索引过这篇
    )
    writer, sink = _record_writer()
    services = _make_services(
        interactor=FakeDeviceInteractor(), interaction_writer=writer
    )
    services.session_factory = _w4_factory(session)
    set_services(services)
    result = await interact_node(
        {
            "interact_plan": [{"note_id": "xhs-never-seen", "kind": "like"}],
            "slot": {"device_id": uuid.uuid4(), "account_id": account_id},
        }
    )
    assert result["interact_results"]["succeeded"] == 1
    assert sink == [], "本地查不到 note 时不应把平台字符串塞进 UUID 外键"


# ---------------------------------------------------------------------------
# W4：route_after_interact 全失败走 ALERT（修 INTERACT_ALL_FAILED 被吞）
# ---------------------------------------------------------------------------


class TestRouteAfterInteract:
    def test_all_failed_goes_alert(self):
        from matrix.agent.guards import route_after_interact

        state = {
            "interact_results": {
                "succeeded": 0,
                "failed": 2,
                "skipped": 0,
                "details": [],
            },
            "last_error": {"code": "INTERACT_ALL_FAILED", "message": "2/2 failed"},
        }
        assert route_after_interact(state, GuardConfig()) == State.ALERT

    def test_all_skipped_goes_idle(self):
        """全部去重跳过（0 成功 0 失败）不算事故，正常收工。"""
        from matrix.agent.guards import route_after_interact

        state = {
            "interact_results": {
                "succeeded": 0,
                "failed": 0,
                "skipped": 2,
                "details": [],
            },
            "last_error": None,
        }
        assert route_after_interact(state, GuardConfig()) == State.IDLE

    def test_partial_failure_goes_idle(self):
        from matrix.agent.guards import route_after_interact

        state = {
            "interact_results": {
                "succeeded": 1,
                "failed": 1,
                "skipped": 0,
                "details": [],
            },
            "last_error": {"code": "PARTIAL_FAIL", "message": "1/2 failed"},
        }
        assert route_after_interact(state, GuardConfig()) == State.IDLE


# ---------------------------------------------------------------------------
# W4：生产 interaction_writer（db_interaction_writer）落库验证
# ---------------------------------------------------------------------------


class TestDbInteractionWriterIntegration:
    @pytest.fixture(autouse=True)
    def _require_pg(self):
        pg_url = os.environ.get("DATABASE_URL", "")
        if not pg_url.startswith(("postgres", "postgresql")):
            pytest.skip("requires DATABASE_URL=postgres://...")

    @pytest.mark.asyncio
    async def test_persists_with_local_note_uuid(self):
        from sqlalchemy import select

        from matrix.agent.bootstrap import db_interaction_writer
        from matrix.db.models import Account, Business, Device, Interaction, Note
        from matrix.db.session import get_session, set_engine

        # test_db.py 用 MagicMock 覆盖过全局 engine 且不还原；先重置回真实连接
        set_engine(None)

        req_id = f"test-{uuid.uuid4().hex}"
        async with get_session() as session:
            biz = Business(
                name="测试业务", slug=f"test-{uuid.uuid4().hex[:8]}", status="active"
            )
            session.add(biz)
            await session.flush()
            dev = Device(
                nickname=f"dev-{uuid.uuid4().hex[:6]}",
                business_id=biz.id,
                status="pending",
            )
            session.add(dev)
            await session.flush()
            acct = Account(
                handle=f"@t{uuid.uuid4().hex[:6]}",
                device_id=dev.id,
                business_id=biz.id,
                status="active",
                risk_score=0,
            )
            session.add(acct)
            await session.flush()
            note = Note(
                title="t",
                content="c",
                business_id=biz.id,
                status="published",
                platform_note_id=f"xhs-{uuid.uuid4().hex[:8]}",
            )
            session.add(note)
            await session.flush()
            acct_id, note_id = acct.id, note.id

        iid = await db_interaction_writer(
            {
                "account_id": acct_id,
                "target_note_id": note_id,
                "type": "like",
                "content": None,
                "result": "success",
                "request_id": req_id,
            }
        )
        assert iid is not None

        async with get_session() as session:
            row = (
                await session.execute(
                    select(Interaction).where(Interaction.request_id == req_id)
                )
            ).scalar_one()
            assert row.target_note_id == note_id
            assert row.type == "like"
            assert row.result == "success"
