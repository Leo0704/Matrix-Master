"""W5：KB 业务隔离真实 DB 集成测试。

- learning_prompt.fetch_relevant_learnings：(business_id = X OR IS NULL)
- Retriever.retrieve：同样的过滤 AND 到向量/关键词两条 SQL 里
"""
from __future__ import annotations

import uuid

import pytest

from matrix.agent.learning_prompt import fetch_relevant_learnings
from matrix.db.models import KbChunk as KbChunkORM
from matrix.db.models import KbDocument as KbDocumentORM
from matrix.kb.retrieval import Retriever


def _mk_doc(*, title: str, business_id, type_: str = "rule") -> KbDocumentORM:
    return KbDocumentORM(
        id=uuid.uuid4(),
        type=type_,
        title=title,
        content=f"{title} 的正文",
        metadata_={},
        version=1,
        is_published=True,
        business_id=business_id,
    )


@pytest.mark.asyncio
async def test_fetch_learnings_scoped_to_business(session, business_factory):
    biz_a = await business_factory(name="A", slug=f"biz-a-{uuid.uuid4().hex[:6]}")
    biz_b = await business_factory(name="B", slug=f"biz-b-{uuid.uuid4().hex[:6]}")

    # 注：kb_documents.business_id 在 DB 层是 NOT NULL（migration 017），
    # "IS NULL = 全局共享" 分支只在约束放宽后才有意义，真实库测不了，只测异业务隔离。
    doc_a = _mk_doc(title="夏季避坑A", business_id=biz_a.id)
    doc_b = _mk_doc(title="夏季避坑B", business_id=biz_b.id)
    session.add_all([doc_a, doc_b])
    await session.flush()

    out = await fetch_relevant_learnings(session, "夏季", business_id=biz_a.id)
    assert "夏季避坑A" in out
    assert "夏季避坑B" not in out  # 其他业务不可见

    # 不传 business_id：老行为，全部可见
    out_all = await fetch_relevant_learnings(session, "夏季")
    assert "夏季避坑B" in out_all


class _FakeEmbedder:
    async def embed_one(self, text, *, model=None):
        return [0.0] * 1536

    async def embed_batch(self, texts, *, model=None):
        return [[0.0] * 1536 for _ in texts]


@pytest.mark.asyncio
async def test_retriever_scoped_to_business(session, business_factory):
    biz_a = await business_factory(name="A", slug=f"biz-a-{uuid.uuid4().hex[:6]}")
    biz_b = await business_factory(name="B", slug=f"biz-b-{uuid.uuid4().hex[:6]}")

    async def _add_doc_with_chunk(title: str, business_id) -> KbDocumentORM:
        doc = _mk_doc(title=title, business_id=business_id)
        session.add(doc)
        await session.flush()
        session.add(
            KbChunkORM(
                id=uuid.uuid4(),
                doc_id=doc.id,
                chunk_index=0,
                text=f"{title} 的正文",
                token_count=10,
                embedding=[0.0] * 1536,
            )
        )
        await session.flush()
        return doc

    doc_a = await _add_doc_with_chunk("夏季规则A", biz_a.id)
    doc_b = await _add_doc_with_chunk("夏季规则B", biz_b.id)

    r = Retriever(session, _FakeEmbedder())
    results = await r.retrieve("夏季", type="rule", top_k=10, business_id=biz_a.id)
    doc_ids = {c.doc_id for c in results}
    assert doc_a.id in doc_ids
    assert doc_b.id not in doc_ids  # 其他业务不可见
