"""粘贴爆款文案 → 拆解 → 写 KB（ingest_viral）测试。

覆盖：
- LLM 返回完整 JSON → 写 1 条 history（已发布）+ 1 张 strategy_card（草稿）
- 无 strategy_updates → 只写 history，返回 card_pending=False
- LLM 挂了 → 兜底用原文写 history，不写 card
- 用户手填 title / metrics 被采用
- 空 raw_text 抛错
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matrix.agent.ingest_viral import ingest_viral_text_to_kb
from matrix.db.models import KbDocument


def _make_kb_doc(*, type_: str = "history", title: str = "t") -> KbDocument:
    return KbDocument(
        id=uuid.uuid4(),
        type=type_,
        ref_id=None,
        title=title,
        content="x",
        metadata_={},
        version=1,
        embedding=[0.0] * 4,
        is_published=type_ == "history",
    )


class TestIngestViralTextToKb:
    async def test_writes_history_and_strategy_card(self):
        llm_json = json.dumps({
            "title": "3天瘦5斤",
            "body": "正文内容……",
            "tags": ["减肥", "干货"],
            "review_text": "标题用数字+痛点，开头直接给结果。",
            "strategy_updates": ["标题带具体数字", "开头先抛结果"],
        })
        history_doc = _make_kb_doc(type_="history")
        card_doc = _make_kb_doc(type_="strategy_card")
        with patch(
            "matrix.agent.ingest_viral.llm_complete",
            AsyncMock(return_value=llm_json),
        ):
            with patch("matrix.agent.ingest_viral.KbStore") as MockStore:
                instance = MockStore.return_value
                instance.create_document = AsyncMock(
                    side_effect=[history_doc, card_doc]
                )
                doc, pending = await ingest_viral_text_to_kb(
                    MagicMock(), MagicMock(), raw_text="随便一段爆款原文"
                )

        assert doc is history_doc
        assert pending is True
        calls = instance.create_document.call_args_list
        assert len(calls) == 2
        # 第一次：history，已发布，内容含点评/套路小节
        assert calls[0].kwargs["type"] == "history"
        assert calls[0].kwargs["is_published"] is True
        assert "## review" in calls[0].kwargs["content"]
        assert "## strategy_updates" in calls[0].kwargs["content"]
        assert calls[0].kwargs["metadata"]["source"] == "external_paste"
        # 第二次：strategy_card，草稿
        assert calls[1].kwargs["type"] == "strategy_card"
        assert calls[1].kwargs["is_published"] is False

    async def test_no_strategy_updates_only_history(self):
        llm_json = json.dumps({
            "title": "标题",
            "body": "正文",
            "tags": [],
            "review_text": "点评",
            "strategy_updates": [],
        })
        with patch(
            "matrix.agent.ingest_viral.llm_complete",
            AsyncMock(return_value=llm_json),
        ):
            with patch("matrix.agent.ingest_viral.KbStore") as MockStore:
                instance = MockStore.return_value
                instance.create_document = AsyncMock(
                    return_value=_make_kb_doc(type_="history")
                )
                _, pending = await ingest_viral_text_to_kb(
                    MagicMock(), MagicMock(), raw_text="原文"
                )
        assert pending is False
        assert instance.create_document.call_count == 1
        assert instance.create_document.call_args.kwargs["type"] == "history"

    async def test_llm_failure_falls_back_to_raw_text(self):
        with patch(
            "matrix.agent.ingest_viral.llm_complete",
            AsyncMock(side_effect=RuntimeError("llm down")),
        ):
            with patch("matrix.agent.ingest_viral.KbStore") as MockStore:
                instance = MockStore.return_value
                instance.create_document = AsyncMock(
                    return_value=_make_kb_doc(type_="history")
                )
                _, pending = await ingest_viral_text_to_kb(
                    MagicMock(), MagicMock(), raw_text="原文兜底"
                )
        # LLM 挂了：只存 history，正文用原文
        assert pending is False
        assert instance.create_document.call_count == 1
        assert "原文兜底" in instance.create_document.call_args.kwargs["content"]

    async def test_user_title_and_metrics_used(self):
        llm_json = json.dumps({
            "title": "AI提炼的标题",
            "body": "正文",
            "tags": [],
            "review_text": "点评",
            "strategy_updates": [],
        })
        with patch(
            "matrix.agent.ingest_viral.llm_complete",
            AsyncMock(return_value=llm_json),
        ):
            with patch("matrix.agent.ingest_viral.KbStore") as MockStore:
                instance = MockStore.return_value
                instance.create_document = AsyncMock(
                    return_value=_make_kb_doc(type_="history")
                )
                await ingest_viral_text_to_kb(
                    MagicMock(),
                    MagicMock(),
                    raw_text="原文",
                    title="我自己填的标题",
                    metrics={"likes": 8888},
                )
        kwargs = instance.create_document.call_args.kwargs
        # 用户手填标题优先于 LLM
        assert kwargs["title"] == "我自己填的标题"
        # metrics 写进正文与 metadata
        assert "likes=8888" in kwargs["content"]
        assert kwargs["metadata"]["metrics"]["likes"] == 8888

    async def test_empty_raw_text_raises(self):
        with pytest.raises(ValueError):
            await ingest_viral_text_to_kb(
                MagicMock(), MagicMock(), raw_text="   "
            )
