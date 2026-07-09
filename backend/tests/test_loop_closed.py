"""闭环集成测试：用 Mock 设备适配器 + Fake LLM/KB 跑通一个完整 goal。

证明 Agent 状态机（RESEARCH→DRAFT→REVIEW→SCHEDULE→DISPATCH→PUBLISH→COLLECT→
ANALYZE→IDLE）在真实节点代码下能端到端收敛，且确实调用了设备发布与回采。
无需真实 LLM / APK / 数据库——边界用 Mock 替代，逻辑全部走生产代码。
"""
from __future__ import annotations

from tests.test_agent import (
    FakeKBRetriever,
    FakeKBWriter,
    FakeLLM,
    InMemoryAgentRepository,
)

from matrix.agent.bootstrap import build_agent_services, build_run_manager
from tests._fake_adapters import MockDeviceAdapter


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
    )
    rm = build_run_manager(services=services, repository=InMemoryAgentRepository())

    run_id = await rm.create_run(goal_text="发一篇夏日穿搭笔记", entry="RESEARCH")
    state = await rm.start_run(run_id)

    # 1) 收敛到终态（ANALYZE → IDLE），无错误
    assert state["current_state"] in ("ANALYZE", "IDLE")
    assert state.get("last_error") is None

    # 2) 闭环确实驱动了设备：发布 + 回采都被调用，且发布内容来自生成的草稿
    assert device.publish_calls, "闭环必须调用设备发布"
    assert device.publish_calls[0]["title"] == "夏日清爽穿搭分享"
    assert device.publish_calls[0]["content"]
    assert device.collect_calls, "闭环必须调用设备回采"

    # 3) 回采指标已写回 state
    metrics = state.get("note_metrics") or {}
    assert "views" in metrics and metrics["views"] >= 0

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
