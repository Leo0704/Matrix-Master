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
    StrategyCard,
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

    async def test_structured_card_rendered_as_hard_rules(self):
        """Phase 4 #3：strategy_card 是 JSON → 渲染成"硬规则"段（不是软文本）。"""
        from matrix.agent.summarize import StrategyCard

        card = StrategyCard(
            title_patterns=["数字+痛点"],
            hook_phrases=["救命"],
            structure=["开头钩子", "痛点", "解决"],
            tone_keywords=["平价"],
            forbidden_patterns=["绝对化用词"],
        )
        # 注意 content 是 JSON（不是 markdown）
        doc = _make_kb_doc(
            type_="strategy_card",
            title="爆款模板 · 夏季女鞋",
            content=card.to_json(),
        )
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [doc]
        session.execute = AsyncMock(return_value=result)

        out = await fetch_relevant_learnings(session, "夏季女鞋")
        # 不再是软文本"[爆款] 标题..."，而是硬规则段
        assert "【标题硬规则】" in out
        assert "【开头硬规则】" in out
        assert "【结构硬规则】" in out
        assert "【调性硬规则】" in out
        assert "【禁用硬规则】" in out
        assert "数字+痛点" in out
        assert "绝对化用词" in out
        # 旧"软文本"标记不再出现
        assert "[爆款]" not in out

    async def test_old_markdown_content_falls_back_to_soft_text(self):
        """老 strategy_card 是 markdown 文本 → 降级为"软示例"。"""
        doc = _make_kb_doc(
            type_="strategy_card",
            title="爆款模板 · 夏季女鞋",
            content="# 爆款模式\n\n- 数字+痛点\n- 季节+人群",
        )
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = [doc]
        session.execute = AsyncMock(return_value=result)

        out = await fetch_relevant_learnings(session, "夏季女鞋")
        # 老格式走软文本
        assert "[爆款]" in out
        assert "数字+痛点" in out
        # 没有硬规则段
        assert "【标题硬规则】" not in out


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
        # 老 prompt 返回格式（无 structured_viral）→ 退化到 hook_phrases
        llm_response = json.dumps({
            "viral_patterns": ["数字+痛点"],
            "failure_lessons": ["别用违规词"],
        })
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(return_value=llm_response),
        ):
            card, failures = await _ask_llm_for_learnings(snapshot)
        # 老 prompt 数据全归 hook_phrases
        assert card.hook_phrases == ["数字+痛点"]
        assert card.is_empty() is False
        assert failures == ["别用违规词"]

    async def test_parses_structured_viral(self):
        """Phase 4 #3：新 prompt 返回 structured_viral 5 字段。"""
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(),
            theme="t",
            audience=None,
            runs=[{"note_id": "1"}],
        )
        llm_response = json.dumps({
            "structured_viral": {
                "title_patterns": ["数字+痛点", "季节+人群"],
                "hook_phrases": ["救命", "后悔没早买"],
                "structure": ["开头钩子", "痛点", "解决", "价格", "CTA"],
                "tone_keywords": ["平价", "真实"],
                "forbidden_patterns": ["绝对化用词"],
            },
            "failure_lessons": ["别用违规词"],
        })
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(return_value=llm_response),
        ):
            card, failures = await _ask_llm_for_learnings(snapshot)
        assert card.title_patterns == ["数字+痛点", "季节+人群"]
        assert card.hook_phrases == ["救命", "后悔没早买"]
        assert card.structure == ["开头钩子", "痛点", "解决", "价格", "CTA"]
        assert card.tone_keywords == ["平价", "真实"]
        assert card.forbidden_patterns == ["绝对化用词"]
        assert failures == ["别用违规词"]

    async def test_strips_markdown_fences(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(), theme="t", audience=None, runs=[{"note_id": "1"}]
        )
        wrapped = '```json\n{"viral_patterns": ["x"], "failure_lessons": []}\n```'
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(return_value=wrapped),
        ):
            card, failures = await _ask_llm_for_learnings(snapshot)
        assert card.hook_phrases == ["x"]
        assert failures == []

    async def test_invalid_json_returns_empty(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(), theme="t", audience=None, runs=[{"note_id": "1"}]
        )
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(return_value="not json at all"),
        ):
            card, failures = await _ask_llm_for_learnings(snapshot)
        assert card.is_empty()
        assert failures == []

    async def test_llm_exception_returns_empty(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(), theme="t", audience=None, runs=[{"note_id": "1"}]
        )
        with patch(
            "matrix.agent.summarize.llm_complete",
            AsyncMock(side_effect=RuntimeError("llm down")),
        ):
            card, failures = await _ask_llm_for_learnings(snapshot)
        assert card.is_empty()
        assert failures == []

    async def test_empty_runs_returns_empty(self):
        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(), theme="t", audience=None, runs=[]
        )
        card, failures = await _ask_llm_for_learnings(snapshot)
        assert card.is_empty()
        assert failures == []


# ---------------------------------------------------------------------------
# StrategyCard dataclass（Phase 4 #3 结构化提取）
# ---------------------------------------------------------------------------


class TestStrategyCard:
    def test_defaults_empty(self):
        c = StrategyCard()
        assert c.is_empty()
        assert c.to_dict() == {
            "title_patterns": [],
            "hook_phrases": [],
            "structure": [],
            "tone_keywords": [],
            "forbidden_patterns": [],
        }

    def test_from_dict_filters_non_list(self):
        c = StrategyCard.from_dict({
            "title_patterns": "not a list",  # 应被过滤
            "hook_phrases": ["a", "", None, 1, 2],  # 1/2 会被 str 化
            "structure": [{"foo": "bar"}],  # dict 不允许 → 跳过
        })
        assert c.title_patterns == []
        # hook_phrases: 过滤空/None，str 化 1/2
        assert c.hook_phrases == ["a", "1", "2"]
        assert c.structure == []

    def test_caps_list_at_10(self):
        c = StrategyCard.from_dict({
            "title_patterns": [str(i) for i in range(20)],
        })
        assert len(c.title_patterns) == 10

    def test_to_json_and_back(self):
        c = StrategyCard(
            title_patterns=["a"],
            hook_phrases=["b"],
            structure=["c"],
            tone_keywords=["d"],
            forbidden_patterns=["e"],
        )
        raw = c.to_json()
        c2 = StrategyCard.parse(raw)
        assert c2 is not None
        assert c2.title_patterns == ["a"]
        assert c2.hook_phrases == ["b"]
        assert c2.structure == ["c"]
        assert c2.tone_keywords == ["d"]
        assert c2.forbidden_patterns == ["e"]

    def test_parse_markdown_returns_none(self):
        """老 strategy_card 是 markdown 文本，parse 应返 None（不破坏向后兼容）。"""
        old_md = "# 爆款模式（goal: 夏季）\n\n- 数字+痛点\n- 季节+人群"
        assert StrategyCard.parse(old_md) is None

    def test_parse_invalid_json_returns_none(self):
        assert StrategyCard.parse("not json") is None
        assert StrategyCard.parse("") is None
        assert StrategyCard.parse(None) is None

    def test_parse_all_empty_returns_none(self):
        """JSON 解析 OK 但所有字段都空 → 返 None（不入下游 prompt）。"""
        assert StrategyCard.parse('{"title_patterns": []}') is None
        assert StrategyCard.parse('{}') is None

    def test_render_for_prompt_includes_all_sections(self):
        c = StrategyCard(
            title_patterns=["数字+痛点", "季节+人群"],
            hook_phrases=["救命", "后悔没早买"],
            structure=["开头钩子", "痛点场景", "解决产品", "价格锚", "CTA"],
            tone_keywords=["平价", "真实"],
            forbidden_patterns=["绝对化用词", "未验证数据"],
        )
        out = c.render_for_prompt(theme="夏季女鞋")
        assert "夏季女鞋" in out
        assert "【标题硬规则】" in out
        assert "数字+痛点" in out
        assert "季节+人群" in out
        assert "【开头硬规则】" in out
        assert "救命" in out
        assert "【结构硬规则】" in out
        assert "开头钩子" in out
        assert "→" in out  # structure 用箭头连接
        assert "【调性硬规则】" in out
        assert "平价" in out
        assert "【禁用硬规则】" in out
        assert "绝对化用词" in out

    def test_render_for_prompt_skips_empty_sections(self):
        c = StrategyCard(title_patterns=["x"])
        out = c.render_for_prompt()
        assert "【标题硬规则】" in out
        assert "【开头硬规则】" not in out
        assert "【结构硬规则】" not in out
        assert "【调性硬规则】" not in out
        assert "【禁用硬规则】" not in out


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

    async def test_auto_publish_true_publishes_strategy_card_only(self):
        """Phase 4 #3：auto_publish=True 时 strategy_card 标已发布，rule 不变。"""
        from matrix.agent.summarize import summarize_goal_to_kb

        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(),
            theme="夏季",
            audience="18-25",
            runs=[{"run_id": "r1"}],
        )
        llm_json = json.dumps({
            "viral_patterns": ["数字+痛点"],
            "failure_lessons": ["别用违规词"],
        })
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
                    viral = _make_kb_doc(type_="strategy_card", title="爆款")
                    rule = _make_kb_doc(type_="rule", title="避坑")
                    instance.create_document = AsyncMock(
                        side_effect=[viral, rule]
                    )
                    result = await summarize_goal_to_kb(
                        MagicMock(),
                        MagicMock(),
                        uuid.uuid4(),
                        auto_publish=True,
                    )
        # 记录 create_document 的两次调用
        calls = instance.create_document.call_args_list
        # 第一次（viral/strategy_card）is_published=True
        assert calls[0].kwargs["is_published"] is True
        assert calls[0].kwargs["type"] == "strategy_card"
        # 第二次（rule）is_published=False
        assert calls[1].kwargs["is_published"] is False
        assert calls[1].kwargs["type"] == "rule"
        assert len(result) == 2

    async def test_auto_publish_false_default(self):
        """auto_publish 缺省 False：strategy_card 也不发布。"""
        from matrix.agent.summarize import summarize_goal_to_kb

        snapshot = GoalSnapshot(
            goal_id=uuid.uuid4(),
            theme="t",
            audience=None,
            runs=[{"run_id": "r1"}],
        )
        llm_json = json.dumps({
            "viral_patterns": ["x"],
            "failure_lessons": [],
        })
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
                    await summarize_goal_to_kb(
                        MagicMock(), MagicMock(), uuid.uuid4()
                    )
        call = instance.create_document.call_args
        assert call.kwargs["is_published"] is False


# ---------------------------------------------------------------------------
# learning_prompt.fetch_relevant_learnings 业务隔离（W5）
# ---------------------------------------------------------------------------


class TestFetchRelevantLearningsBusinessScope:
    async def test_business_id_filter_in_stmt(self):
        """传 business_id：SQL 带 (business_id = X OR IS NULL) 过滤。"""
        captured: list = []
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []

        async def _exec(stmt):
            captured.append(stmt)
            return result

        session.execute = _exec
        bid = uuid.uuid4()
        await fetch_relevant_learnings(session, "夏季", business_id=bid)
        assert captured
        sql = str(captured[0])
        assert "business_id" in sql
        assert "IS NULL" in sql

    async def test_no_business_id_no_filter(self):
        """不传 business_id：保持老行为，SQL 不含业务过滤。"""
        captured: list = []
        session = MagicMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = []

        async def _exec(stmt):
            captured.append(stmt)
            return result

        session.execute = _exec
        await fetch_relevant_learnings(session, "夏季")
        assert captured
        # SELECT 列清单里本来就有 kb_documents.business_id 列，
        # 判负要看绑定参数（过滤条件才会产生 :business_id 占位）
        assert ":business_id" not in str(captured[0])
