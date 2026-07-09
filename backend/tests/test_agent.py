"""matrix.agent 单元测试。

mock LLM / KB / Device，不发真实网络请求；用 InMemoryAgentRepository
替代真实 DB（避开 sqlaclchemy ORM + sqlite 的复杂 setup）。

覆盖：
- guards：每个 guard 多 input
- state machine：9 主状态 + 转移边可触发
- nodes：research / draft / review / revise / schedule / dispatch / publish / collect / analyze / alert
- prompts：template format 正确
- run_manager：create / cancel / get status / resume ack
- repository（in-memory）：write → read round-trip
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from matrix.agent._services import (
    AgentServices,
    reset_services,
    set_services,
)
from matrix.agent.guards import (
    GuardConfig,
    can_review_to_alert,
    can_review_to_revise,
    can_review_to_schedule,
    research_has_candidates,
    review_verdict,
    route_after_collect,
    route_after_dispatch,
    route_after_publish,
    route_after_research,
    route_after_review,
    route_after_revise,
    route_after_schedule,
    route_idle,
)
from matrix.agent.nodes import (
    alert_node,
    analyze_node,
    collect_node,
    dispatch_node,
    draft_node,
    publish_node,
    research_node,
    review_node,
    revise_node,
    schedule_node,
)
from matrix.agent.prompts import (
    ANALYZE_USER,
    DRAFT_SYSTEM,
    DRAFT_USER,
    RESEARCH_USER,
    REVIEW_SYSTEM,
    REVIEW_USER,
)
from matrix.agent.protocols import (
    DevicePublisher,
    KBRetriever,
    KBWriter,
    RetrievedChunk,
    RetrieveQuery,
)
from matrix.agent.repository import AgentCheckpointRow, AgentRunRow
from matrix.agent.run_manager import RunManager
from matrix.agent.state_machine import build_state_machine
from matrix.agent.types import (
    AgentState,
    ReviewFailureReason,
    RunStatus,
    State,
)
from matrix.llm.clients import CompletionResult, LLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fake LLM
# ---------------------------------------------------------------------------


class FakeLLM(LLMClient):
    """按 prompt/system 关键字返回不同 JSON。"""

    provider = "fake"

    def __init__(self, *, mapping: dict[str, str] | None = None, default: str = "{}") -> None:
        self.mapping = mapping or {}
        self.default = default

    async def complete(
        self,
        prompt: str,
        *,
        model: str = "fake",
        max_tokens: int = 1024,
        temperature: float = 1.0,
        system: str | None = None,
        call_type: str = "generation",
        run_id: str | None = None,
        account_id: str | None = None,
        timeout: float = 60.0,
    ) -> CompletionResult:
        for key, val in self.mapping.items():
            if (key in prompt) or (system and key in system):
                text = val
                break
        else:
            text = self.default
        return CompletionResult(
            text=text,
            model=model,
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
            provider=self.provider,
            stop_reason="end_turn",
        )


class FakeKBRetriever:
    def __init__(self, *, mapping: dict[tuple[str, str], list[RetrievedChunk]] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls: list[RetrieveQuery] = []

    async def retrieve(self, query: RetrieveQuery) -> list[RetrievedChunk]:
        self.calls.append(query)
        key = (query.doc_types[0] if query.doc_types else "", query.query)
        if key in self.mapping:
            return self.mapping[key]
        return [
            RetrievedChunk(
                chunk_id=uuid4(),
                doc_id=uuid4(),
                doc_type=query.doc_types[0] if query.doc_types else "topic",
                text=f"fake chunk for {query.query}",
                score=0.5,
                metadata={},
            )
        ]


class FakeKBWriter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def upsert_document(
        self,
        *,
        doc_type: str,
        ref_id: UUID | None,
        title: str | None,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> UUID:
        self.calls.append(
            {
                "doc_type": doc_type,
                "title": title,
                "content": content,
                "metadata": metadata or {},
            }
        )
        return uuid4()


@dataclass
class _PubResult:
    ok: bool
    note_id: UUID = field(default_factory=uuid4)
    platform_note_id: str | None = "p123"
    platform_url: str | None = "https://example.com/note/p123"
    error_code: str | None = None
    error_message: str | None = None


class FakeDevicePublisher:
    def __init__(self, *, ok: bool = True, error_code: str | None = None) -> None:
        self.ok = ok
        self.error_code = error_code
        self.calls: list[dict[str, Any]] = []

    async def publish(self, **kwargs):
        self.calls.append(kwargs)
        return _PubResult(ok=self.ok, error_code=self.error_code)


class FakeDeviceCollector:
    def __init__(self, *, metrics: dict[str, int] | None = None, fail: bool = False) -> None:
        self.metrics = metrics or {
            "views": 100,
            "likes": 8,
            "collects": 3,
            "comments": 2,
            "follows_gained": 1,
        }
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def collect(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("simulated collect failure")
        return dict(self.metrics)


_NOTIFY_LOG: list[tuple[str, dict[str, Any]]] = []


async def recording_notifier(name: str, payload: dict[str, Any]) -> None:
    _NOTIFY_LOG.append((name, payload))


def clear_notify_log() -> None:
    _NOTIFY_LOG.clear()


# ---------------------------------------------------------------------------
# In-memory Repository
# ---------------------------------------------------------------------------


@dataclass
class _MemRun(AgentRunRow):
    id: UUID
    goal_id: UUID | None
    current_state: str
    payload: dict[str, Any] | None
    status: str
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = field(default=None)


@dataclass
class _MemCheckpoint(AgentCheckpointRow):
    run_id: UUID
    ts: datetime
    from_state: str
    to_state: str
    payload: dict[str, Any] | None


class InMemoryAgentRepository:
    def __init__(self) -> None:
        self._runs: dict[UUID, _MemRun] = {}
        self._checkpoints: list[_MemCheckpoint] = []

    async def create_run(
        self,
        *,
        run_id: UUID,
        goal_id: UUID | None,
        payload: dict[str, Any],
        started_at: datetime,
        current_state: str,
        status: str,
    ) -> None:
        if run_id in self._runs:
            raise ValueError(f"run exists: {run_id}")
        self._runs[run_id] = _MemRun(
            id=run_id,
            goal_id=goal_id,
            current_state=current_state,
            payload=payload,
            status=status,
            started_at=started_at,
            updated_at=started_at,
            ended_at=None,
        )

    async def write_checkpoint(
        self,
        *,
        run_id: UUID,
        from_state: str,
        to_state: str,
        payload: dict[str, Any] | None = None,
        ts: datetime | None = None,
    ) -> None:
        self._checkpoints.append(
            _MemCheckpoint(
                run_id=run_id,
                ts=ts or datetime.now(UTC),
                from_state=from_state,
                to_state=to_state,
                payload=payload,
            )
        )

    async def read_last_checkpoint(self, run_id: UUID) -> _MemCheckpoint | None:
        rows = [c for c in self._checkpoints if c.run_id == run_id]
        if not rows:
            return None
        return max(rows, key=lambda c: c.ts)

    async def read_all_checkpoints(self, run_id: UUID) -> list[_MemCheckpoint]:
        return sorted([c for c in self._checkpoints if c.run_id == run_id], key=lambda c: c.ts)

    async def get_run(self, run_id: UUID) -> _MemRun | None:
        return self._runs.get(run_id)

    async def update_run(
        self,
        run_id: UUID,
        *,
        current_state: str | None = None,
        status: str | None = None,
        payload_merge: dict[str, Any] | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"run not found: {run_id}")
        if current_state is not None:
            run.current_state = current_state
        if status is not None:
            run.status = status
        if payload_merge is not None:
            run.payload = {**(run.payload or {}), **payload_merge}
        if ended_at is not None:
            run.ended_at = ended_at
        run.updated_at = datetime.now(UTC)


# ---------------------------------------------------------------------------
# factories
# ---------------------------------------------------------------------------


def make_services(
    *,
    llm: LLMClient | None = None,
    kb: KBRetriever | None = None,
    writer: KBWriter | None = None,
    publisher: DevicePublisher | None = None,
    collector: FakeDeviceCollector | None = None,
    task_writer: Any = None,
) -> AgentServices:
    return AgentServices(
        llm=llm or FakeLLM(),
        kb_retriever=kb or FakeKBRetriever(),
        kb_writer=writer or FakeKBWriter(),
        device_publisher=publisher or FakeDevicePublisher(),
        device_collector=collector or FakeDeviceCollector(),
        notifier=recording_notifier,
        task_writer=task_writer,
    )


@pytest.fixture(autouse=True)
def _reset_services():
    reset_services()
    clear_notify_log()
    yield
    reset_services()
    clear_notify_log()


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


class TestGuards:
    def test_research_has_candidates(self):
        assert research_has_candidates({"candidates": [{"title": "t"}]}) is True
        assert research_has_candidates({"candidates": []}) is False

    def test_review_pass_when_clean(self):
        cfg = GuardConfig()
        state: AgentState = {
            "review": {
                "passed": False,
                "forbidden_hits": [],
                "score_dup": 0.4,
                "score_human": 0.8,
            }
        }
        assert can_review_to_schedule(state, cfg) is True
        assert can_review_to_revise(state, cfg) is False
        assert can_review_to_alert(state, cfg) is False

    def test_review_fail_forbidden(self):
        cfg = GuardConfig()
        state: AgentState = {
            "review": {
                "forbidden_hits": ["badword"],
                "score_dup": 0.0,
                "score_human": 1.0,
            }
        }
        assert can_review_to_schedule(state, cfg) is False
        verdict = review_verdict(state, cfg)
        assert ReviewFailureReason.FORBIDDEN_WORD.value in verdict["reasons"]

    def test_review_fail_dup(self):
        cfg = GuardConfig()
        state: AgentState = {
            "review": {
                "forbidden_hits": [],
                "score_dup": 0.9,
                "score_human": 1.0,
            }
        }
        verdict = review_verdict(state, cfg)
        assert ReviewFailureReason.DUPLICATE.value in verdict["reasons"]
        assert can_review_to_schedule(state, cfg) is False

    def test_review_fail_human(self):
        cfg = GuardConfig()
        state: AgentState = {
            "review": {
                "forbidden_hits": [],
                "score_dup": 0.0,
                "score_human": 0.3,
            }
        }
        verdict = review_verdict(state, cfg)
        assert ReviewFailureReason.LOW_HUMAN_SCORE.value in verdict["reasons"]

    def test_review_revise_below_max(self):
        cfg = GuardConfig(revise_max_attempts=3)
        state: AgentState = {
            "review": {"forbidden_hits": ["x"], "score_dup": 0.0, "score_human": 1.0},
            "revise_attempts": 1,
        }
        assert can_review_to_revise(state, cfg) is True
        assert can_review_to_alert(state, cfg) is False

    def test_review_alert_at_max(self):
        cfg = GuardConfig(revise_max_attempts=3)
        state: AgentState = {
            "review": {"forbidden_hits": ["x"], "score_dup": 0.0, "score_human": 1.0},
            "revise_attempts": 4,
        }
        assert can_review_to_revise(state, cfg) is False
        assert can_review_to_alert(state, cfg) is True

    def test_routing_functions(self):
        cfg = GuardConfig()
        assert route_after_research({"candidates": []}, cfg) == State.ALERT
        assert route_after_research({"candidates": [{"title": "x"}]}, cfg) == State.DRAFT
        s_pass: AgentState = {"review": {"forbidden_hits": [], "score_dup": 0.0, "score_human": 1.0}}
        assert route_after_review(s_pass, cfg) == State.SCHEDULE
        s_fail: AgentState = {"review": {"forbidden_hits": ["x"], "score_dup": 0.0, "score_human": 1.0}, "revise_attempts": 0}
        assert route_after_review(s_fail, cfg) == State.REVISE
        s_alert: AgentState = {"review": {"forbidden_hits": ["x"], "score_dup": 0.0, "score_human": 1.0}, "revise_attempts": 10}
        assert route_after_review(s_alert, cfg) == State.ALERT
        assert route_after_revise({"revise_attempts": 0}, cfg) == State.DRAFT
        assert route_after_revise({"revise_attempts": 4}, cfg) == State.ALERT
        assert route_after_schedule({"slot": None}, cfg) == State.ALERT
        assert route_after_schedule({"slot": {"device_id": "d", "account_id": "a"}}, cfg) == State.DISPATCH
        assert route_after_dispatch({"created_task_ids": []}, cfg) == State.ALERT
        assert route_after_dispatch({"created_task_ids": ["x"]}, cfg) == State.PUBLISH
        assert route_after_publish({"publish_result": {"ok": True}}, cfg) == State.COLLECT
        assert route_after_publish({"publish_result": {"ok": False}}, cfg) == State.ALERT
        assert route_after_collect({"note_metrics": {"views": 1}}, cfg) == State.ANALYZE
        assert route_after_collect({"note_metrics": {}}, cfg) == State.ALERT
        assert route_idle({"entry": "RESEARCH"}, cfg) == State.RESEARCH
        assert route_idle({"entry": "ANALYZE"}, cfg) == State.ANALYZE


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_research_prompt_format(self):
        out = RESEARCH_USER.format(
            goal="g", brand="b", persona="p", history="h", rules="r", today="2026-07-09"
        )
        assert "g" in out and "b" in out and "p" in out and "h" in out and "r" in out
        assert "2026-07-09" in out  # 日期占位符注入

    def test_draft_prompt_format(self):
        out = DRAFT_USER.format(
            topic_title="tt",
            topic_rationale="tr",
            persona_style="ps",
            persona_tone="pt",
            forbidden_words="",
            brand="br",
            product_facts="",
        )
        assert "tt" in out and "tr" in out
        assert DRAFT_SYSTEM.format(persona_name="pn").startswith("你是")

    def test_review_prompt_format(self):
        out = REVIEW_USER.format(
            title="ttl",
            content="ctt",
            forbidden_words="",
            similar_history="none",
            dup_threshold=0.5,
            human_threshold=0.5,
        )
        assert "ttl" in out
        assert "{dup_threshold}" in REVIEW_SYSTEM or "相似度<" in REVIEW_SYSTEM

    def test_analyze_prompt_format(self):
        out = ANALYZE_USER.format(
            title="t",
            content="c",
            tags=["a"],
            views=1,
            likes=2,
            collects=3,
            comments=4,
            follows_gained=5,
            persona_style="p",
            rules="r",
        )
        assert "views=1" in out and "follows_gained=5" in out


# ---------------------------------------------------------------------------
# nodes
# ---------------------------------------------------------------------------


class TestNodes:
    @pytest.mark.asyncio
    async def test_research_produces_candidates_with_llm(self):
        llm = FakeLLM(
            mapping={
                "选题": json.dumps({"selected": [{"title": "夏季穿搭", "rationale": "命中 hot"}]})
            }
        )
        kb = FakeKBRetriever()
        set_services(make_services(llm=llm, kb=kb))
        result = await research_node({"goal_text": "主题", "revise_attempts": 0})
        assert result["candidates"][0]["title"] == "夏季穿搭"
        assert result["selected_topic"] is not None

    @pytest.mark.asyncio
    async def test_research_llm_failure_returns_alert_payload(self):
        class Boom(LLMClient):
            provider = "fake"

            async def complete(self, *a, **kw):
                raise RuntimeError("boom")

        set_services(make_services(llm=Boom(), kb=FakeKBRetriever()))
        result = await research_node({"goal_text": "x"})
        assert result["candidates"] == []
        assert result["last_error"]["code"] == "LLM_FAILED"

    @pytest.mark.asyncio
    async def test_draft_produces_payload(self):
        llm = FakeLLM(
            mapping={
                "你是小红书爆款文案写手": json.dumps(
                    {"title": "夏日穿搭 3 招", "content": "上周去海边...", "tags": ["穿搭", "夏日"]}
                )
            }
        )
        set_services(make_services(llm=llm, kb=FakeKBRetriever()))
        result = await draft_node(
            {"selected_topic": {"title": "夏日穿搭", "rationale": "hot"}, "research_chunks": []}
        )
        assert result["draft"]["title"] == "夏日穿搭 3 招"
        assert result["draft"]["tags"] == ["穿搭", "夏日"]
        assert result["last_error"] is None

    @pytest.mark.asyncio
    async def test_review_passes(self):
        llm = FakeLLM(
            mapping={
                "你是内容审核员": json.dumps(
                    {
                        "forbidden_hits": [],
                        "score_dup": 0.3,
                        "score_human": 0.9,
                        "passed": True,
                        "reason": "ok",
                    }
                )
            }
        )
        set_services(make_services(llm=llm))
        result = await review_node({"draft": {"title": "t", "content": "c"}})
        assert result["review"]["passed"] is True
        assert result["review"]["forbidden_hits"] == []

    @pytest.mark.asyncio
    async def test_review_local_forbidden_override(self):
        llm = FakeLLM(
            mapping={
                "你是内容审核员": json.dumps({"passed": True, "forbidden_hits": []})
            }
        )
        kb = FakeKBRetriever(
            mapping={
                ("rule", "title\ncontent 中包含违禁词X"): [
                    RetrievedChunk(
                        chunk_id=uuid4(),
                        doc_id=uuid4(),
                        doc_type="rule",
                        text="[forbidden] 违禁词X",
                        score=0.9,
                        metadata={},
                    )
                ]
            }
        )
        set_services(make_services(llm=llm, kb=kb))
        result = await review_node({"draft": {"title": "title", "content": "content 中包含违禁词X"}})
        assert result["review"]["passed"] is False
        assert "违禁词X" in result["review"]["forbidden_hits"]

    @pytest.mark.asyncio
    async def test_revise_increments_attempts(self):
        llm = FakeLLM(
            mapping={
                "按要求严格改写": json.dumps({"title": "改写后", "content": "新", "tags": ["a"]})
            }
        )
        set_services(make_services(llm=llm))
        result = await revise_node(
            {
                "draft": {"title": "old", "content": "old", "tags": []},
                "review": {"forbidden_hits": ["X"]},
                "revise_attempts": 0,
            }
        )
        assert result["revise_attempts"] == 1
        assert result["draft"]["title"] == "改写后"

    @pytest.mark.asyncio
    async def test_schedule_synthetic_slot(self):
        set_services(make_services())
        result = await schedule_node({"draft": {"title": "t"}})
        assert result["slot"] is not None
        assert result["slot"]["reason"] == "synthetic_slot"

    @pytest.mark.asyncio
    async def test_dispatch_creates_task(self):
        captured = []

        async def writer(rec: dict[str, Any]):
            captured.append(rec)

        set_services(make_services(task_writer=writer))
        result = await dispatch_node(
            {
                "draft": {"title": "t", "content": "c", "tags": ["a"]},
                "slot": {"device_id": str(uuid4()), "account_id": str(uuid4())},
                "scheduled_at": "2026-07-08T10:00:00+00:00",
            }
        )
        assert len(result["created_task_ids"]) == 1
        assert len(captured) == 1
        assert captured[0]["action"] == "device_publish"

    @pytest.mark.asyncio
    async def test_publish_success(self):
        publisher = FakeDevicePublisher(ok=True)
        set_services(make_services(publisher=publisher))
        result = await publish_node(
            {
                "created_task_ids": ["t1"],
                "draft": {"title": "t", "content": "c", "tags": ["a"], "images": []},
                "slot": {"device_id": str(uuid4()), "account_id": str(uuid4())},
            }
        )
        assert result["publish_result"]["ok"] is True
        assert result["publish_result"]["platform_note_id"] == "p123"

    @pytest.mark.asyncio
    async def test_publish_failure_routes_to_alert(self):
        publisher = FakeDevicePublisher(ok=False, error_code="RISK_BLOCKED")
        set_services(make_services(publisher=publisher))
        result = await publish_node(
            {
                "created_task_ids": ["t1"],
                "draft": {"title": "t", "content": "c", "tags": ["a"], "images": []},
                "slot": {"device_id": str(uuid4()), "account_id": str(uuid4())},
            }
        )
        assert result["publish_result"]["ok"] is False
        assert result["last_error"]["code"] == "RISK_BLOCKED"

    @pytest.mark.asyncio
    async def test_collect_succeeds(self):
        collector = FakeDeviceCollector(
            metrics={"views": 50, "likes": 2, "collects": 1, "comments": 1, "follows_gained": 0}
        )
        set_services(make_services(collector=collector))
        result = await collect_node(
            {
                "publish_result": {"platform_note_id": "p1"},
                "slot": {"device_id": str(uuid4()), "account_id": str(uuid4())},
            }
        )
        assert result["note_metrics"]["views"] == 50

    @pytest.mark.asyncio
    async def test_analyze_writes_history(self):
        llm = FakeLLM(
            mapping={
                "你是运营复盘员": json.dumps(
                    {"review_text": "爆款", "strategy_updates": ["多用 emoji", "结尾加 CTA"]}
                )
            }
        )
        writer = FakeKBWriter()
        set_services(make_services(llm=llm, writer=writer))
        result = await analyze_node(
            {
                "draft": {"title": "t", "content": "c", "tags": ["a"]},
                "note_metrics": {"views": 100, "likes": 5, "collects": 1, "comments": 2, "follows_gained": 0},
            }
        )
        assert result["last_error"] is None
        assert len(writer.calls) == 1
        assert writer.calls[0]["doc_type"] == "history"
        assert "爆款" in writer.calls[0]["content"]

    @pytest.mark.asyncio
    async def test_alert_emits_notification(self):
        set_services(make_services())
        result = await alert_node(
            {
                "run_id": uuid4(),
                "last_error": {"code": "PUBLISH_FAILED", "message": "denied"},
                "current_state": "PUBLISH",
            }
        )
        assert any(p["code"] == "PUBLISH_FAILED" for (_, p) in _NOTIFY_LOG)
        assert result["last_error"] is None


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_compile(self):
        sm = build_state_machine()
        graph = sm.compile()
        assert graph is not None

    @pytest.mark.asyncio
    async def test_full_happy_path(self):
        llm = FakeLLM(
            mapping={
                "选题": json.dumps({"selected": [{"title": "t", "rationale": "r"}]}),
                "你是小红书爆款文案写手": json.dumps(
                    {"title": "draft_t", "content": "draft_c", "tags": ["t1"]}
                ),
                "你是内容审核员": json.dumps(
                    {
                        "forbidden_hits": [],
                        "score_dup": 0.1,
                        "score_human": 0.9,
                        "passed": True,
                        "reason": "ok",
                    }
                ),
                "你是运营复盘员": json.dumps(
                    {"review_text": "ok", "strategy_updates": ["a"]}
                ),
            }
        )
        writer = FakeKBWriter()
        publisher = FakeDevicePublisher(ok=True)
        set_services(make_services(llm=llm, writer=writer, publisher=publisher))
        sm = build_state_machine()
        result = await sm.ainvoke(
            {
                "entry": "RESEARCH",
                "goal_text": "我的目标",
                "revise_attempts": 0,
                "created_task_ids": [],
            }
        )
        assert result["current_state"] in (State.IDLE.value, State.ANALYZE.value)
        assert len(publisher.calls) == 1
        assert any(c["doc_type"] == "history" for c in writer.calls)

    @pytest.mark.asyncio
    async def test_research_empty_routes_to_alert(self):
        """无 KB chunks + LLM 无输出 → candidates 为空 → 转 ALERT。"""

        class EmptyKB(KBRetriever):
            async def retrieve(self, query):
                return []

        EmptyKB()  # mypy
        llm = FakeLLM(mapping={"选题": "{}"}, default="{}")
        set_services(make_services(llm=llm, kb=EmptyKB()))
        sm = build_state_machine()
        result = await sm.ainvoke(
            {
                "entry": "RESEARCH",
                "goal_text": "x",
                "revise_attempts": 0,
                "created_task_ids": [],
            }
        )
        assert result["current_state"] == State.ALERT.value
        assert result.get("candidates") == []
        assert any(name == "agent.alert" for (name, _) in _NOTIFY_LOG)


# ---------------------------------------------------------------------------
# repository + run manager
# ---------------------------------------------------------------------------


class TestRepository:
    @pytest.mark.asyncio
    async def test_write_and_read_checkpoint_roundtrip(self):
        repo = InMemoryAgentRepository()
        run_id = uuid4()
        await repo.create_run(
            run_id=run_id,
            goal_id=None,
            payload={"goal_text": "x"},
            started_at=datetime.now(UTC),
            current_state="IDLE",
            status="running",
        )
        await repo.write_checkpoint(
            run_id=run_id,
            from_state="IDLE",
            to_state="RESEARCH",
            payload={"foo": "bar"},
        )
        last = await repo.read_last_checkpoint(run_id)
        assert last is not None
        assert last.from_state == "IDLE"
        assert last.to_state == "RESEARCH"
        assert last.payload == {"foo": "bar"}

        all_cps = await repo.read_all_checkpoints(run_id)
        assert len(all_cps) == 1
        # 再写一次 → 应有两条，且 read_last 返回 ts 较新的
        await repo.write_checkpoint(
            run_id=run_id,
            from_state="DRAFT",
            to_state="REVIEW",
            payload={},
        )
        last2 = await repo.read_last_checkpoint(run_id)
        assert last2 is not None
        assert last2.to_state == "REVIEW"

    @pytest.mark.asyncio
    async def test_create_run_then_get_run(self):
        repo = InMemoryAgentRepository()
        run_id = uuid4()
        await repo.create_run(
            run_id=run_id,
            goal_id=None,
            payload={"x": 1},
            started_at=datetime.now(UTC),
            current_state="IDLE",
            status="running",
        )
        run = await repo.get_run(run_id)
        assert run is not None
        assert run.current_state == "IDLE"
        assert run.payload == {"x": 1}

    @pytest.mark.asyncio
    async def test_update_run(self):
        repo = InMemoryAgentRepository()
        run_id = uuid4()
        await repo.create_run(
            run_id=run_id,
            goal_id=None,
            payload={"a": 1},
            started_at=datetime.now(UTC),
            current_state="IDLE",
            status="running",
        )
        await repo.update_run(run_id, current_state="RESEARCH", payload_merge={"last_state": "RESEARCH"})
        run = await repo.get_run(run_id)
        assert run.current_state == "RESEARCH"
        assert run.payload == {"a": 1, "last_state": "RESEARCH"}


@pytest.fixture
def in_memory_repo() -> InMemoryAgentRepository:
    return InMemoryAgentRepository()


class TestRunManager:
    @pytest.mark.asyncio
    async def test_create_run(self, in_memory_repo):
        services = make_services()
        rm = RunManager(services=services, repository=in_memory_repo)
        run_id = await rm.create_run(goal_text="x", entry="RESEARCH")
        run = await in_memory_repo.get_run(run_id)
        assert run is not None
        assert run.payload["goal_text"] == "x"
        all_cps = await in_memory_repo.read_all_checkpoints(run_id)
        # 至少有创建时写的 IDLE→IDLE 起点
        assert len(all_cps) == 1

    @pytest.mark.asyncio
    async def test_cancel_run(self, in_memory_repo):
        services = make_services()
        rm = RunManager(services=services, repository=in_memory_repo)
        run_id = await rm.create_run()
        await rm.cancel_run(run_id)
        run = await in_memory_repo.get_run(run_id)
        assert run.status == RunStatus.CANCELLED.value
        assert run.ended_at is not None

    @pytest.mark.asyncio
    async def test_get_run_status(self, in_memory_repo):
        services = make_services()
        rm = RunManager(services=services, repository=in_memory_repo)
        run_id = await rm.create_run(goal_text="hello", entry="RESEARCH")
        status = await rm.get_run_status(run_id)
        assert status is not None
        assert status["id"] == str(run_id)
        assert status["payload"]["goal_text"] == "hello"
        assert status["last_checkpoint"] is not None
        assert status["last_checkpoint"]["to_state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_get_run_status_not_found(self, in_memory_repo):
        services = make_services()
        rm = RunManager(services=services, repository=in_memory_repo)
        status = await rm.get_run_status(uuid4())
        assert status is None

    @pytest.mark.asyncio
    async def test_resume_run_with_alert_ack(self, in_memory_repo):
        """mock state_machine.ainvoke：alert_ack=False → 停在 ALERT；True → 回 IDLE。

        第 2 次 resume 前要把 run status 重置为 RUNNING（因为 mock 把 status 标 SUCCESS 了）。
        """
        services = make_services()
        rm = RunManager(services=services, repository=in_memory_repo)
        run_id = await rm.create_run(goal_text="x", entry="RESEARCH")

        async def mock_ainvoke(state):
            state["_alert_ack"] = bool(state.get("_alert_ack"))
            # 模拟根据 ack 决定终点 + 把"上一轮停在 alert"改回 RUNNING 以便续跑
            ack = bool(state.get("_alert_ack"))
            state["current_state"] = State.IDLE.value if ack else State.ALERT.value
            # 故意不修改 status，让 run_manager.update_run 决定
            return state

        rm.sm.ainvoke = mock_ainvoke  # type: ignore[assignment]

        # 第 1 次：不带 ack → 停在 ALERT
        out = await rm.resume_run(run_id)
        assert out["current_state"] == State.ALERT.value
        run = await in_memory_repo.get_run(run_id)
        assert run.status == RunStatus.SUCCESS.value  # mock 没出错 → SUCCESS

        # 续跑需要 status=Running；手动回滚
        await in_memory_repo.update_run(run_id, status=RunStatus.RUNNING.value)

        # 第 2 次：ack=True → 回到 IDLE
        out2 = await rm.resume_run(run_id, alert_ack=True)
        assert out2["current_state"] == State.IDLE.value

    @pytest.mark.asyncio
    async def test_top_level_convenience_functions(self, in_memory_repo, monkeypatch):
        from matrix.agent import run_manager as rm_mod

        services = make_services()
        rm = RunManager(services=services, repository=in_memory_repo)
        rm_mod.init_manager(rm)

        run_id = await rm_mod.create_run(goal_text="hi", entry="RESEARCH")
        assert isinstance(run_id, UUID)
        status = await rm_mod.get_run_status(run_id)
        assert status is not None
        await rm_mod.cancel_run(run_id)
        status2 = await rm_mod.get_run_status(run_id)
        assert status2["status"] == RunStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# protocol / types  sanity
# ---------------------------------------------------------------------------


class TestProtocolsSanity:
    def test_kb_retriever_protocol(self):
        assert isinstance(FakeKBRetriever(), KBRetriever)

    def test_kb_writer_protocol(self):
        assert isinstance(FakeKBWriter(), KBWriter)

    def test_device_publisher_protocol(self):
        assert isinstance(FakeDevicePublisher(), DevicePublisher)
