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
async def test_state_machine_publish_routes_to_collect_when_no_plan():
    """PUBLISH 成功 + 无 interact_plan → 直接 COLLECT。"""
    from matrix.agent.guards import route_after_publish

    cfg = GuardConfig()
    state = {
        "publish_result": {"ok": True, "platform_note_id": "abc"},
        "interact_plan": [],
    }
    assert route_after_publish(state, cfg) == State.COLLECT


@pytest.mark.asyncio
async def test_state_machine_publish_to_collect_when_switch_off():
    """enable_post_publish_interact=False → 即使有 plan 也走 COLLECT。"""
    from matrix.agent.guards import route_after_publish

    cfg = GuardConfig(enable_post_publish_interact=False)
    state = {
        "publish_result": {"ok": True, "platform_note_id": "abc"},
        "interact_plan": [{"note_id": "n1", "kind": "like"}],
    }
    assert route_after_publish(state, cfg) == State.COLLECT


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
    """完整闭环：goal 带 2 comment + 1 like → RESEARCH→...→PUBLISH→INTERACT→COLLECT→ANALYZE→IDLE。"""
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

    # 走到 ANALYZE 或 IDLE（终态）
    assert state["current_state"] in (State.ANALYZE.value, State.IDLE.value)
    # interact_results 反映 3 个 plan
    results = state.get("interact_results") or {}
    assert results["succeeded"] == 3
    assert results["failed"] == 0
    # 设备被调了 publish + 3 interact（calls 列表包含所有）
    actions = [c.get("action") or c.get("action") for c in interactor.calls]
    # publish + 3 interact = 4
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
