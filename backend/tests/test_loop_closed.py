"""闭环集成测试：用 Mock 设备适配器 + Fake LLM/KB 跑通一个完整 goal。

证明 Agent 状态机（RESEARCH→DRAFT→REVIEW→SCHEDULE→DISPATCH→PUBLISH→COLLECT→
ANALYZE→IDLE）在真实节点代码下能端到端收敛，且确实调用了设备发布与回采。
无需真实 LLM / APK / 数据库——边界用 Mock 替代，逻辑全部走生产代码。
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from tests.test_agent import (
    FakeKBRetriever,
    FakeKBWriter,
    FakeLLM,
    InMemoryAgentRepository,
)

from matrix.agent.bootstrap import build_agent_services, build_run_manager
from matrix.agent.protocols import ChosenSlot
from tests._fake_adapters import MockDeviceAdapter


class _FakeSlotPicker:
    """测试用：每次 choose_slot 返回一个固定的 ChosenSlot。"""

    def __init__(self) -> None:
        self.device_id = uuid4()
        self.account_id = uuid4()

    async def choose_slot(
        self, *, draft: dict, persona_config: dict | None = None, now: datetime | None = None
    ) -> ChosenSlot:
        return ChosenSlot(
            device_id=self.device_id,
            account_id=self.account_id,
            reason="slot_picker.match",
            scheduled_at=now or datetime.utcnow(),
        )


class _FakeRoundAllocator:
    """测试用：goal/round 扇出场景的 allocator；预置 N 个 slot。"""

    def __init__(self, slots: list[ChosenSlot] | None = None) -> None:
        self._slots = slots or []
        self.allocate_calls: list[dict] = []
        self.count_calls = 0

    async def count_active_devices(self, *, business_id=None) -> int:
        self.count_calls += 1
        return len(self._slots)

    async def allocate(
        self,
        *,
        brief: dict,
        n: int,
        base_time: datetime | None = None,
        stagger_minutes: int = 15,
        persona_config: dict | None = None,
        business_id=None,
    ) -> list[ChosenSlot]:
        self.allocate_calls.append(
            {
                "brief": brief,
                "n": n,
                "base_time": base_time,
                "stagger_minutes": stagger_minutes,
                "persona_config": persona_config,
                "business_id": business_id,
            }
        )
        return self._slots[:n]

    async def is_slot_valid(
        self, *, device_id, account_id, business_id=None, now: datetime | None = None
    ) -> bool:
        return any(
            s.device_id == device_id and s.account_id == account_id
            for s in self._slots
        )


async def test_closed_loop_runs_end_to_end():
    # LLM 按阶段返回正确 JSON 形状（REVIEW 必须 passed:true 才能过守卫）
    llm = FakeLLM(
        mapping={
            "你是选题研究员": '{"selected":[{"title":"夏日清爽穿搭分享","rationale":"应季热点"}]}',
            "你是小红书爆款文案写手": '{"title":"夏日清爽穿搭分享","content":"今年夏天清爽穿搭分享，三件套搞定。","tags":["穿搭","夏日","OOTD"]}',
            "你是内容审核员": '{"forbidden_hits":[],"score_dup":0.1,"score_human":0.9,"passed":true,"reason":"ok"}',
            "你是运营复盘员": '{"review_text":"表现不错","strategy_updates":["增加图文比例"]}',
        }
    )
    device = MockDeviceAdapter()
    services = build_agent_services(
        llm=llm,
        kb_retriever=FakeKBRetriever(),
        kb_writer=FakeKBWriter(),
        device_adapter=device,
        scheduler=_FakeSlotPicker(),
    )
    # 活跃窗测试把窗口设成全天，避免受 datetime.now(UTC) 影响（容器时区+8h）
    services.system_metadata = {
        "persona_config": {"active_window": {"start": 0, "end": 24}}
    }
    rm = build_run_manager(services=services, repository=InMemoryAgentRepository())

    run_id = await rm.create_run(goal_text="发一篇夏日穿搭笔记", entry="RESEARCH")
    state = await rm.start_run(run_id)

    # 1) 收敛到终态 IDLE（v0.7+ 主链不再走 COLLECT→ANALYZE），无错误
    assert state["current_state"] == "IDLE"
    assert state.get("last_error") is None

    # 2) 闭环确实驱动了设备发布，且发布内容来自生成的草稿
    assert device.publish_calls, "闭环必须调用设备发布"
    assert device.publish_calls[0]["title"] == "夏日清爽穿搭分享"
    assert device.publish_calls[0]["content"]

    # 3) v0.7+ 主链不再即时回采，collect 不应被调用（真复盘由 24h 后独立 ANALYZE run 完成）
    assert not device.collect_calls

    # 4) run 记录为成功
    status = await rm.get_run_status(run_id)
    assert status["status"] == "success"


async def test_closed_loop_fails_when_publish_fails():
    """设备发布失败应转 ALERT，run 记为失败（证明闭环的失败路径也通）。"""
    llm = FakeLLM(
        mapping={
            "你是选题研究员": '{"selected":[{"title":"x","rationale":"y"}]}',
            "你是小红书爆款文案写手": '{"title":"x","content":"c","tags":["t"]}',
            "你是内容审核员": '{"forbidden_hits":[],"score_dup":0.1,"score_human":0.9,"passed":true,"reason":"ok"}',
            "你是运营复盘员": '{"review_text":"","strategy_updates":[]}',
        }
    )
    device = MockDeviceAdapter(publish_ok=False, publish_error_code="MOCK_FAIL")
    services = build_agent_services(
        llm=llm,
        kb_retriever=FakeKBRetriever(),
        kb_writer=FakeKBWriter(),
        device_adapter=device,
        scheduler=_FakeSlotPicker(),
    )
    rm = build_run_manager(services=services, repository=InMemoryAgentRepository())

    run_id = await rm.create_run(goal_text="g", entry="RESEARCH")
    state = await rm.start_run(run_id)

    assert state["current_state"] == "ALERT"
    # ALERT 节点会清 last_error，但会把错误快照到 last_error_snapshot
    assert state.get("last_error_snapshot") is not None
    assert state.get("last_error_snapshot", {}).get("code")
    status = await rm.get_run_status(run_id)
    assert status["status"] == "failed"
