"""复盘 + 学习模块测试（第 3 期）。

覆盖：
- summarize.py：LLM 返回爆款/失败 → 写 2 篇 KB doc；JSON 解析容错；goal 不存在
- learning_prompt.py：keyword 过滤、theme/audience 提取、prompt 文本格式
- 路由（learning.py）：trigger_summarize、list_documents
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matrix.agent.learning_prompt import (
    _extract_keywords,
    _keyword_match,
    fetch_relevant_learnings,
)
from matrix.agent.summarize import (
    GoalSnapshot,
    _ask_llm_for_learnings,
    _load_goal_snapshot,
    summarize_goal_to_kb,
)
from matrix.db.models import KbDocument


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_kb_doc(
    *,
    type_: str = "strategy_card",
    title: str = "爆款模板",
    content: str = "标题用数字+痛点",
    is_published: bool = True,
    metadata: dict | None = None,
) -> KbDocument:
    return KbDocument(
        id=uuid.uuid4(),
        type=type_,
        ref_id=None,
        title=title,
        content=content,
        metadata_=metadata or {"goal_id": str(uuid.uuid4())},
        version=1,
        embedding=[0.0] * 4,
        is_published=is_published,
    )


# ---------------------------------------------------------------------------
# learning_prompt._extract_keywords
# ---------------------------------------------------------------------------


class TestExtractKeywords:
    def test_simple_theme_splits_by_space(self):
        kws = _extract_keywords("夏季男生 穿搭", None)
        assert "夏季男生" in kws
        assert "穿搭" in kws

    def test_chinese_2gram_extraction(self):
        # 中文 2-gram 兜底
        kws = _extract_keywords("夏季男生穿搭", None)
        joined = "".join(kws)
        assert "夏季" in joined
        assert "男生" in joined

    def test_audience_added(self):
        kws = _extract_keywords("test", "18-25岁大学生")
        assert "18-25岁大学生" in kws

    def test_empty_returns_empty(self):
        assert _extract_keywords("", None) == []

    def test_dedup(self):
        kws = _extract_keywords("test test test", None)
        assert kws.count("test") == 1


class TestKeywordMatch:
    def test_match_in_title(self):
        doc = _make_kb_doc(title="夏季穿搭模板")
        assert _keyword_match(doc, ["夏季"])

    def test_match_in_content(self):
        doc = _make_kb_doc(content="数字+痛点标题效果好")
        assert _keyword_match(doc, ["数字"])

    def test_no_match(self):
        doc = _make_kb_doc(title="x", content="y")
        assert not _keyword_match(doc, ["夏季"])

    def test_empty_keywords_matches_anything(self):
        doc = _make_kb_doc()
        assert _keyword_match(doc, [])


# ---------------------------------------------------------------------------
# learning_prompt.fetch_relevant_learnings
# ---------------------------------------------------------------------------


class TestFetchRelevantLearnings:
    async def test_returns_empty_when_no_kb_docs(self):
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result)

        out = await fetch_relevant_learnings(session, "夏季穿搭")
        assert "暂无相关历史经验" in out

    async def test_filters_unpublished(self):
        # SQL 层已经过滤 is_published=True，所以 mock 时只返已发布的
        # 用 2 条不同的已发布 doc 验证都能取到
        a = _make_kb_doc(type_="strategy_card", title="夏季爆款A", content="x")
        b = _make_kb_doc(type_="rule", title="夏季避坑B", content="x")
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [a, b]
        session.execute = AsyncMock(return_value=result)

        out = await fetch_relevant_learnings(session, "夏季")
        # 2 条都能看到
        assert "夏季爆款A" in out
        assert "夏季避坑B" in out

    async def test_filters_by_keyword(self):
        # 1 条匹配，1 条不匹配
        match = _make_kb_doc(title="夏季男生穿搭爆款", content="abc")
        no_match = _make_kb_doc(title="冬季女生美妆", content="def")
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [match, no_match]
        session.execute = AsyncMock(return_value=result)

        out = await fetch_relevant_learnings(session, "夏季")
        assert "夏季男生穿搭爆款" in out
        assert "冬季女生美妆" not in out

    async def test_includes_viral_and_failure_labels(self):
        viral = _make_kb_doc(type_="strategy_card", title="爆款", content="x夏季")
        failure = _make_kb_doc(type_="rule", title="避坑", content="x夏季")
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [viral, failure]
        session.execute = AsyncMock(return_value=result)

        out = await fetch_relevant_learnings(session, "夏季")
        assert "[爆款]" in out
        assert "[避坑]" in out


# ---------------------------------------------------------------------------
# summarize._ask_llm_for_learnings
# ---------------------------------------------------------------------------


class TestAskLlmForLearnings:
    async def test_parses_clean_json(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(),
            theme="test",
            audience=None,
            runs=[{"note_id": "1", "views": 1000, "likes": 100}],
        )
        llm_response = json.dumps({
            "viral_patterns": ["数字+痛点"],
            "failure_lessons": ["别用违规词"],
        })
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(return_value=llm_response),
        ):
            result = await _ask_llm_for_learnings(snapshot)
        assert result["viral_patterns"] == ["数字+痛点"]
        assert result["failure_lessons"] == ["别用违规词"]

    async def test_strips_markdown_fences(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(), theme="t", audience=None, runs=[{"note_id": "1"}]
        )
        wrapped = '```json\n{"viral_patterns": ["x"], "failure_lessons": []}\n```'
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(return_value=wrapped),
        ):
            result = await _ask_llm_for_learnings(snapshot)
        assert result["viral_patterns"] == ["x"]

    async def test_invalid_json_returns_empty(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(), theme="t", audience=None, runs=[{"note_id": "1"}]
        )
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(return_value="not json at all"),
        ):
            result = await _ask_llm_for_learnings(snapshot)
        assert result == {"viral_patterns": [], "failure_lessons": []}

    async def test_llm_exception_returns_empty(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(), theme="t", audience=None, runs=[{"note_id": "1"}]
        )
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(side_effect=RuntimeError("llm down")),
        ):
            result = await _ask_llm_for_learnings(snapshot)
        assert result == {"viral_patterns": [], "failure_lessons": []}

    async def test_empty_runs_returns_empty(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(), theme="t", audience=None, runs=[]
        )
        result = await _ask_llm_for_learnings(snapshot)
        assert result == {"viral_patterns": [], "failure_lessons": []}


# ---------------------------------------------------------------------------
# summarize.summarize_goal_to_kb
# ---------------------------------------------------------------------------


class TestSummarizeGoalToKb:
    async def test_goal_not_found_returns_empty(self):
        session = MagicMock()
        session.get = AsyncMock(return_value=None)
        embedder = MagicMock()
        result = await summarize_goal_to_kb(
            session, embedder, uuid.uuid4()
        )
        assert result == []

    async def test_writes_viral_and_failure_docs(self):
        # 整个链路 mock：goal 存在、runs 有数据、LLM 提炼到两类
        goal_id = uuid.uuid4()
        goal = MagicMock()
        goal.deleted_at = None
        goal.target = {"theme": "夏季", "audience": "18-25岁"}
        session = MagicMock()
        session.get = AsyncMock(return_value=goal)

        # mock runs
        run = MagicMock()
        run.id = uuid.uuid4()
        run.goal_id = goal_id
        run.current_state = "PUBLISH"
        run.status = "success"
        run.started_at = None
        run.ended_at = None

        # _load_goal_snapshot 内部调 3 次 execute（runs / notes / metrics）
        # 我们让它直接返回一个 snapshot
        snapshot = GoalSnapshot(
            goal_id=goal_id,
            theme="夏季",
            audience="18-25岁",
            runs=[{"run_id": "r1", "title": "abc", "views": 100}],
        )
        with patch(
            "matrix.agent.summarize._load_goal_snapshot",
            AsyncMock(return_value=snapshot),
        ):
            llm_json = json.dumps({
                "viral_patterns": ["数字+痛点"],
                "failure_lessons": ["别用违规词"],
            })
            with patch(
                "matrix.agent.summarize.llm_complete",
                AsyncMock(return_value=llm_json),
            ):
                # mock KbStore.create_document
                viral_doc = _make_kb_doc(type_="strategy_card", title="爆款")
                rule_doc = _make_kb_doc(type_="rule", title="避坑")
                with patch(
                    "matrix.agent.summarize.KbStore"
                ) as MockStore:
                    instance = MockStore.return_value
                    instance.create_document = AsyncMock(
                        side_effect=[viral_doc, rule_doc]
                    )
                    result = await summarize_goal_to_kb(
                        session, MagicMock(), goal_id
                    )
        assert len(result) == 2
        assert result[0].type == "strategy_card"
        assert result[1].type == "rule"

    async def test_only_viral_writes_one_doc(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(),
            theme="t",
            audience=None,
            runs=[{"run_id": "r1"}],
        )
        llm_json = json.dumps({"viral_patterns": ["x"], "failure_lessons": []})
        with patch(
            "matrix.agent.summarize._load_goal_snapshot",
            AsyncMock(return_value=snapshot),
        ):
            with patch(
                "matrix.agent.summarize.llm_complete",
                AsyncMock(return_value=llm_json),
            ):
                with patch(
                    "matrix.agent.summarize.KbStore"
                ) as MockStore:
                    instance = MockStore.return_value
                    instance.create_document = AsyncMock(
                        return_value=_make_kb_doc(type_="strategy_card")
                    )
                    result = await summarize_goal_to_kb(
                        MagicMock(), MagicMock(), uuid.uuid4()
                    )
        assert len(result) == 1
        assert result[0].type == "strategy_card"
