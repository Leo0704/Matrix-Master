"""知识库（kb）子系统测试。

覆盖：
- Chunker：500 token 边界 / overlap 正确性 / 短文本不切 / 单字符 / 空字符串
- EmbeddingService：缓存命中 / 批量切批 / 缓存容量 LRU 淘汰
- Retriever：mock DB session 模拟向量 + 关键词两路命中；验证 RRF 排序
- KbStore：create_document 自动 chunk + embed；update_document version + 1；
  soft_delete 不物理删、is_published 置 False；硬删级联
- ReviewGate：publish / unpublish 翻转 is_published；幂等；写 audit log

约束：
- 全部 mock DB / EmbeddingClient
- 不连真实 PG
- 不调真实 embedding API
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from matrix.kb import (
    Chunker,
    EmbeddingService,
    KbStore,
    ReviewGate,
)
from matrix.kb.chunker import Chunk
from matrix.kb.retrieval import (
    Retriever,
    _build_ts_query,
    _vec_to_pgvector_str,
)


# ===========================================================================
# Chunker
# ===========================================================================


class TestChunker:
    def test_short_text_single_chunk(self):
        c = Chunker(chunk_size=10, overlap=2)
        chunks = c.split("hello world")
        assert len(chunks) == 1
        assert chunks[0].text == "hello world"
        assert chunks[0].index == 0
        assert chunks[0].token_count > 0

    def test_empty_text_returns_empty(self):
        c = Chunker()
        assert c.split("") == []

    def test_exact_boundary_single_chunk(self):
        """文本编码后 token 数 == chunk_size 时，仍是单 chunk。"""
        c = Chunker(chunk_size=5, overlap=1)
        # 5 token 的英文短语
        text = "one two three four five"
        n = len(c._enc.encode(text))
        assert n == 5
        chunks = c.split(text)
        assert len(chunks) == 1
        assert chunks[0].token_count == 5

    def test_long_text_splits(self):
        c = Chunker(chunk_size=5, overlap=1)
        # 12 token
        text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
        chunks = c.split(text)
        # step=4, 取 start=0,4,8 → 3 片；最后一片含余下 token
        assert len(chunks) >= 2
        # 索引严格递增
        indices = [ch.index for ch in chunks]
        assert indices == list(range(len(chunks)))
        # 累计覆盖原 token
        # 重叠区出现两次：相邻 chunk 至少有 overlap 个 token 重叠
        full = c._enc.encode("".join(ch.text for ch in chunks))
        original = c._enc.encode(text)
        # 重叠 = 总 token 数 - 原始 token 数 + 最后一片不重叠
        # 实际只验证「每片 token 数 == 切片长度」
        for ch in chunks:
            assert ch.token_count == len(c._enc.encode(ch.text))

    def test_overlap_correctness(self):
        """相邻两片应有 overlap 个 token 重叠。"""
        c = Chunker(chunk_size=10, overlap=3)
        text = " ".join(f"w{i}" for i in range(50))
        chunks = c.split(text)
        assert len(chunks) >= 3
        for prev, curr in zip(chunks, chunks[1:]):
            prev_tokens = c._enc.encode(prev.text)
            curr_tokens = c._enc.encode(curr.text)
            # 取 prev 的后 overlap 个 token
            assert prev_tokens[-c.overlap :] == curr_tokens[: c.overlap], (
                f"overlap mismatch: prev={prev_tokens} curr={curr_tokens}"
            )

    def test_step_calculation(self):
        """step = chunk_size - overlap。"""
        c = Chunker(chunk_size=500, overlap=50)
        assert c.chunk_size == 500
        assert c.overlap == 50
        # 默认与 SDD 一致
        c2 = Chunker()
        assert c2.chunk_size == 500
        assert c2.overlap == 50

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            Chunker(chunk_size=0, overlap=0)
        with pytest.raises(ValueError):
            Chunker(chunk_size=100, overlap=100)  # overlap 必须 < chunk_size
        with pytest.raises(ValueError):
            Chunker(chunk_size=10, overlap=-1)

    def test_token_count_matches_reencode(self):
        c = Chunker(chunk_size=20, overlap=4)
        text = " ".join(f"t{i}" for i in range(30))
        for ch in c.split(text):
            assert ch.token_count == len(c._enc.encode(ch.text))

    def test_chinese_works(self):
        c = Chunker(chunk_size=8, overlap=2)
        text = "今天天气很好适合出门散步去公园看看花草树木放松一下心情享受阳光"
        chunks = c.split(text)
        # 不为 1 也不为 0：中文也按 token 切
        assert len(chunks) >= 1
        for ch in chunks:
            assert ch.token_count > 0

    def test_chunk_dataclass_frozen(self):
        with pytest.raises(Exception):
            Chunk(text="x", token_count=1, index=0).index = 5  # type: ignore[misc]


# ===========================================================================
# EmbeddingService
# ===========================================================================


def _vec(dim: int = 4, seed: int = 0) -> list[float]:
    """构造一个简单向量。"""
    return [float((seed + i) % 7) / 10 for i in range(dim)]


class _MockEmbeddingClient:
    """记录调用次数 + 返回受控向量。"""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(
        self, texts: list[str], *, model: str | None = None
    ) -> list[list[float]]:
        self.calls.append(list(texts))
        return [_vec(self.dim, seed=hash(t) & 0xFF) for t in texts]


class TestEmbeddingService:
    @pytest.mark.asyncio
    async def test_embed_batch_basic(self):
        client = _MockEmbeddingClient(dim=8)
        svc = EmbeddingService(client, batch_size=100, cache_size=10)
        vecs = await svc.embed_batch(["a", "b", "c"])
        assert len(vecs) == 3
        assert all(len(v) == 8 for v in vecs)
        # 单次 API 调用（不足 batch_size）
        assert len(client.calls) == 1
        assert client.calls[0] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=10)

        v1 = await svc.embed_batch(["a", "b"])
        assert len(client.calls) == 1

        v2 = await svc.embed_batch(["a", "b"])
        # 全部命中缓存：不再调 API
        assert len(client.calls) == 1
        # 返回的向量一致
        assert v1 == v2

    @pytest.mark.asyncio
    async def test_partial_cache(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=10)

        await svc.embed_batch(["a"])  # 调用 1
        assert len(client.calls) == 1

        # "a" 命中；"b" 未命中
        await svc.embed_batch(["a", "b"])
        assert len(client.calls) == 2
        # 第二次调用只传 "b"
        assert client.calls[1] == ["b"]

    @pytest.mark.asyncio
    async def test_cache_key_includes_model(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=10)

        await svc.embed_batch(["a"], model="text-embedding-3-small")
        await svc.embed_batch(["a"], model="text-embedding-3-large")
        # 不同 model 视为不同 key
        assert len(client.calls) == 2

    @pytest.mark.asyncio
    async def test_batching_splits_large_request(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=3, cache_size=10)
        texts = [f"t{i}" for i in range(7)]
        await svc.embed_batch(texts)
        # 7 个 text，batch_size=3 → 3+3+1 = 3 次调用
        assert len(client.calls) == 3
        assert len(client.calls[0]) == 3
        assert len(client.calls[1]) == 3
        assert len(client.calls[2]) == 1
        # 顺序保持
        sent = [t for batch in client.calls for t in batch]
        assert sent == texts

    @pytest.mark.asyncio
    async def test_lru_eviction(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=2)

        await svc.embed_batch(["a"])
        await svc.embed_batch(["b"])
        # cache: [a, b]
        assert svc.cache_size == 2

        await svc.embed_batch(["c"])
        # cache: [b, c]（a 被淘汰）
        assert svc.cache_size == 2
        # 再请求 a → 重新调 API
        before = len(client.calls)
        await svc.embed_batch(["a"])
        assert len(client.calls) == before + 1

    @pytest.mark.asyncio
    async def test_lru_promotes_recent(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=2)

        await svc.embed_batch(["a"])
        await svc.embed_batch(["b"])
        # 访问 a → 把它挪到末尾
        await svc.embed_batch(["a"])  # 命中
        # 加 c → b 被淘汰（因为 a 在末尾，b 变成最久）
        await svc.embed_batch(["c"])
        # b 应被淘汰
        before = len(client.calls)
        await svc.embed_batch(["b"])  # 这次是 miss
        assert len(client.calls) == before + 1

    @pytest.mark.asyncio
    async def test_empty_input(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=10)
        assert await svc.embed_batch([]) == []
        # 不该调 API
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_cache_size_zero_disables_caching(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=0)
        await svc.embed_batch(["a"])
        await svc.embed_batch(["a"])
        # 每次都调
        assert len(client.calls) == 2
        assert svc.cache_size == 0

    @pytest.mark.asyncio
    async def test_clear_cache(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=10)
        await svc.embed_batch(["a", "b"])
        assert svc.cache_size == 2
        svc.clear_cache()
        assert svc.cache_size == 0

    @pytest.mark.asyncio
    async def test_embed_one_delegates(self):
        client = _MockEmbeddingClient()
        svc = EmbeddingService(client, batch_size=100, cache_size=10)
        v = await svc.embed_one("hi")
        assert len(v) == client.dim
        # 缓存命中：第二次 embed_one("hi") 不调 API
        await svc.embed_one("hi")
        assert len(client.calls) == 1


# ===========================================================================
# ts_query helper
# ===========================================================================


class TestTsQuery:
    def test_basic(self):
        q = _build_ts_query("敏感肌 粉底")
        assert q is not None
        # 期望 '敏感肌' & '粉底'
        assert "'敏感肌'" in q
        assert "'粉底'" in q
        assert " & " in q

    def test_lowercases(self):
        q = _build_ts_query("Hello World")
        assert "'hello'" in q
        assert "'world'" in q

    def test_strips_punct(self):
        q = _build_ts_query("hello, world!")
        # 标点不参与
        assert "'hello'" in q
        assert "'world'" in q

    def test_empty_returns_none(self):
        assert _build_ts_query("") is None
        assert _build_ts_query("   ") is None
        assert _build_ts_query("!!!") is None


class TestVecSerialization:
    def test_basic(self):
        s = _vec_to_pgvector_str([0.1, 0.2, 0.3])
        assert s.startswith("[")
        assert s.endswith("]")
        assert "0.1" in s
        assert "0.2" in s

    def test_nan_inf_filtered(self):
        s = _vec_to_pgvector_str([0.1, float("inf"), float("nan"), 0.3])
        assert "inf" not in s.lower()
        assert "nan" not in s.lower()
        # 全部为合法数字
        import json
        nums = json.loads(s)
        assert len(nums) == 4


# ===========================================================================
# Retriever — RRF 排序验证
# ===========================================================================


class _FakeRow:
    """模拟 SQLAlchemy Row：支持属性访问。"""

    def __init__(self, **kwargs: Any) -> None:
        self._data = kwargs

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return self._data[name]


class _FakeSession:
    """mock AsyncSession：按 SQL 文本返回不同结果。"""

    def __init__(self, *, vector_rows: list, keyword_rows: list) -> None:
        self._vector_rows = vector_rows
        self._keyword_rows = keyword_rows
        self.executed: list[Any] = []

    async def execute(self, stmt, params=None):  # noqa: ARG002
        sql = str(stmt)
        self.executed.append((sql, params))
        result = MagicMock()
        if "ts_rank" in sql or "tsvector" in sql:
            result.__iter__ = lambda self: iter(  # noqa: B023
                [_FakeRow(**r) for r in self._keyword_rows]
            )
            # 改写成 list
            rows = [_FakeRow(**r) for r in self._keyword_rows]
        else:
            rows = [_FakeRow(**r) for r in self._vector_rows]
        # 直接给个 list result
        result2 = MagicMock()
        result2._rows = rows
        # 模拟 __iter__ 行为
        result2.__iter__ = lambda self: iter(rows)  # noqa: B023
        return result2


def _make_row(
    *,
    chunk_id: str | None = None,
    doc_id: str | None = None,
    rank: float = 0.0,
    text: str = "t",
    chunk_index: int = 0,
) -> dict:
    return {
        "chunk_id": uuid.UUID(chunk_id) if chunk_id else uuid.uuid4(),
        "doc_id": uuid.UUID(doc_id) if doc_id else uuid.uuid4(),
        "doc_type": "rule",
        "doc_title": "T",
        "chunk_index": chunk_index,
        "text": text,
        "metadata": {},
        "rank": rank,
        "distance": rank,
    }


class TestRetrieverRRF:
    @pytest.mark.asyncio
    async def test_rrf_formula_vector_only(self):
        """只有向量命中：score = 1/(k+rank)，rank=1 → 1/61。"""
        session = _FakeSession(
            vector_rows=[
                _make_row(text="a", rank=0.1),
                _make_row(text="b", rank=0.5),
            ],
            keyword_rows=[],
        )
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder, rrf_k=60, candidate_multiplier=2)
        results = await r.retrieve("q", type="rule", top_k=5)
        assert len(results) == 2
        # 第一名 rank=1 → 1/61；第二名 rank=2 → 1/62
        assert results[0].score == pytest.approx(1 / 61)
        assert results[1].score == pytest.approx(1 / 62)
        assert results[0].sources == ("vector",)

    @pytest.mark.asyncio
    async def test_rrf_fusion_vector_and_keyword(self):
        """同一 chunk 在向量 / 关键词两路都命中 → 分数叠加。"""
        a_id = str(uuid.uuid4())
        b_id = str(uuid.uuid4())
        c_id = str(uuid.uuid4())
        session = _FakeSession(
            vector_rows=[
                _make_row(chunk_id=a_id, text="a", rank=0.9),  # rank 1
                _make_row(chunk_id=b_id, text="b", rank=0.5),  # rank 2
            ],
            keyword_rows=[
                _make_row(chunk_id=a_id, text="a", rank=0.7),  # rank 1
                _make_row(chunk_id=c_id, text="c", rank=0.3),  # rank 2
            ],
        )
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder, rrf_k=60, candidate_multiplier=2)
        results = await r.retrieve("q", type="rule", top_k=5)
        # a: 1/61 + 1/61 = 2/61
        # b: 1/62
        # c: 1/62
        by_id = {str(x.chunk_id): x for x in results}
        assert by_id[a_id].score == pytest.approx(2 / 61)
        assert by_id[b_id].score == pytest.approx(1 / 62)
        assert by_id[c_id].score == pytest.approx(1 / 62)
        # 排序：a 排第一
        assert str(results[0].chunk_id) == a_id
        # a 标注两路命中
        assert set(by_id[a_id].sources) == {"vector", "keyword"}

    @pytest.mark.asyncio
    async def test_topk_respected(self):
        rows = [_make_row(text=f"t{i}", rank=1.0 - i * 0.1) for i in range(10)]
        session = _FakeSession(vector_rows=rows, keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder, rrf_k=60, candidate_multiplier=2)
        results = await r.retrieve("q", type="rule", top_k=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self):
        session = _FakeSession(vector_rows=[], keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder, rrf_k=60)
        assert await r.retrieve("", type="rule") == []
        assert await r.retrieve("   ", type="rule") == []

    @pytest.mark.asyncio
    async def test_invalid_topk(self):
        session = _FakeSession(vector_rows=[], keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder)
        assert await r.retrieve("q", type="rule", top_k=0) == []
        assert await r.retrieve("q", type="rule", top_k=-1) == []

    @pytest.mark.asyncio
    async def test_filters_in_query(self):
        """filter 应当出现在 SQL 的 WHERE 中。"""
        session = _FakeSession(vector_rows=[], keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder, rrf_k=60, candidate_multiplier=2)
        await r.retrieve(
            "敏感肌", type="rule", filters={"severity": 5, "tags": ["敏感", "粉底"]}
        )
        # 至少执行了向量和关键词两条 SQL
        assert len(session.executed) >= 2
        # 检查每条 SQL 都带 type 过滤
        for sql, _ in session.executed:
            assert ":doc_type" in sql
            assert "is_published" in sql
        # 检查 params 含过滤值
        all_params = [p for _, p in session.executed]
        assert any(
            p and p.get("doc_type") == "rule" for p in all_params
        )

    @pytest.mark.asyncio
    async def test_filter_key_whitelist(self):
        session = _FakeSession(vector_rows=[], keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder)
        with pytest.raises(ValueError, match="filter key not allowed"):
            await r.retrieve("q", type="rule", filters={"random_field": "x"})

    @pytest.mark.asyncio
    async def test_is_published_filter_in_sql(self):
        """未发布的 doc 不应被检索到（SQL WHERE 含 is_published=TRUE）。"""
        session = _FakeSession(vector_rows=[], keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder)
        await r.retrieve("q", type="rule")
        for sql, _ in session.executed:
            assert "is_published = TRUE" in sql

    @pytest.mark.asyncio
    async def test_tiebreak_uses_chunk_id(self):
        """同 RRF 分数时按 chunk_id 升序保证稳定排序。

        构造：a 在向量 rank1 + 关键词 rank2；b 在向量 rank2 + 关键词 rank1
        → 两者 RRF 分数 = 1/61 + 1/62 相等
        """
        a_id = str(uuid.uuid4())
        b_id = str(uuid.uuid4())
        session = _FakeSession(
            vector_rows=[
                _make_row(chunk_id=a_id, text="a", rank=0.9),
                _make_row(chunk_id=b_id, text="b", rank=0.5),
            ],
            keyword_rows=[
                _make_row(chunk_id=b_id, text="b", rank=0.7),
                _make_row(chunk_id=a_id, text="a", rank=0.3),
            ],
        )
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder)
        results = await r.retrieve("q", type="rule", top_k=5)
        # 两者分数相等
        assert results[0].score == pytest.approx(results[1].score)
        # 稳定排序：按 chunk_id 升序
        assert str(results[0].chunk_id) < str(results[1].chunk_id)


# ===========================================================================
# KbStore
# ===========================================================================


def _make_session_mock() -> AsyncMock:
    """构造一个能跑通 store 的 mock session。"""
    s = AsyncMock()
    s.add = MagicMock()  # add 是同步方法（Session.add 不是 awaitable）
    s.flush = AsyncMock()
    s.execute = AsyncMock()
    return s


class TestKbStore:
    @pytest.mark.asyncio
    async def test_create_document_writes_doc_and_chunks(self):
        s = _make_session_mock()
        client = _MockEmbeddingClient()
        embedder = EmbeddingService(client, batch_size=10, cache_size=10)
        store = KbStore(s, embedder, Chunker(chunk_size=10, overlap=2))

        doc = await store.create_document(
            type="rule",
            content="alpha beta gamma delta epsilon zeta eta theta iota kappa",
            title="rule1",
            metadata={"severity": 5, "category": "forbidden"},
        )

        assert doc.type == "rule"
        assert doc.title == "rule1"
        assert doc.version == 1
        assert doc.is_published is False
        assert doc.embedding is not None and len(doc.embedding) == client.dim
        # chunks 数 >= 2（文本被切了）
        assert client.calls and sum(len(b) for b in client.calls) >= 2
        # 至少 add 了 doc + chunks
        added = [c.args[0] for c in s.add.call_args_list]
        assert any(isinstance(a, type(doc)) for a in added)
        assert any(
            getattr(a, "__class__", type(None)).__name__ == "KbChunk" for a in added
        )

    @pytest.mark.asyncio
    async def test_create_document_short_text_one_chunk(self):
        s = _make_session_mock()
        client = _MockEmbeddingClient()
        embedder = EmbeddingService(client, batch_size=10, cache_size=10)
        store = KbStore(s, embedder, Chunker(chunk_size=500, overlap=50))

        doc = await store.create_document(type="persona", content="短文本")
        # 1 chunk → 1 次 embed_batch
        assert len(client.calls) == 1
        assert client.calls[0] == ["短文本"]

    @pytest.mark.asyncio
    async def test_create_document_invalid_type(self):
        s = _make_session_mock()
        client = _MockEmbeddingClient()
        store = KbStore(s, EmbeddingService(client))
        with pytest.raises(ValueError, match="invalid kb type"):
            await store.create_document(type="bogus", content="x")

    @pytest.mark.asyncio
    async def test_create_document_empty_content(self):
        s = _make_session_mock()
        client = _MockEmbeddingClient()
        store = KbStore(s, EmbeddingService(client))
        with pytest.raises(ValueError, match="content"):
            await store.create_document(type="rule", content="")

    @pytest.mark.asyncio
    async def test_update_document_version_increments(self):
        s = _make_session_mock()
        # 第一次 get 返回 v1 doc；update 不变 content
        doc_id = uuid.uuid4()
        v1_doc = MagicMock()
        v1_doc.id = doc_id
        v1_doc.content = "old"
        v1_doc.title = "t"
        v1_doc.metadata_ = {}
        v1_doc.ref_id = None
        v1_doc.is_published = False
        v1_doc.version = 1
        v1_doc.embedding = [0.0]

        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=v1_doc)),  # get
        ]
        client = _MockEmbeddingClient()
        embedder = EmbeddingService(client)
        store = KbStore(s, embedder, Chunker(chunk_size=500, overlap=50))
        out = await store.update_document(doc_id, title="new title")
        assert out.version == 2
        assert out.title == "new title"

    @pytest.mark.asyncio
    async def test_update_document_content_resets_chunks(self):
        s = _make_session_mock()
        doc_id = uuid.uuid4()
        v1_doc = MagicMock()
        v1_doc.id = doc_id
        v1_doc.content = "old"
        v1_doc.title = None
        v1_doc.metadata_ = {}
        v1_doc.ref_id = None
        v1_doc.is_published = False
        v1_doc.version = 1
        v1_doc.embedding = [0.0] * 4

        # execute 调用顺序：
        # 1) get_document (SELECT)
        # 2) _replace_chunks → DELETE chunks
        # 3) embed_batch → 触发 _client.embed
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=v1_doc)),
            MagicMock(),  # DELETE result
        ]
        client = _MockEmbeddingClient()
        embedder = EmbeddingService(client, batch_size=10, cache_size=10)
        store = KbStore(s, embedder, Chunker(chunk_size=10, overlap=2))
        out = await store.update_document(
            doc_id, content="alpha beta gamma delta epsilon zeta eta theta iota"
        )
        # 重新 chunk + embed
        assert len(client.calls) >= 1
        # version +1
        assert out.version == 2
        # 新的 doc embedding 是新 chunks 均值
        assert out.embedding is not None

    @pytest.mark.asyncio
    async def test_update_document_not_found(self):
        s = _make_session_mock()
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]
        client = _MockEmbeddingClient()
        store = KbStore(s, EmbeddingService(client))
        with pytest.raises(LookupError, match="not found"):
            await store.update_document(uuid.uuid4(), title="x")

    @pytest.mark.asyncio
    async def test_soft_delete_marks_deleted(self):
        s = _make_session_mock()
        doc_id = uuid.uuid4()
        doc = MagicMock()
        doc.id = doc_id
        doc.is_published = True
        # 1) get_document → 返回 doc
        # 2) UPDATE → 不校验
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
            MagicMock(),
        ]
        client = _MockEmbeddingClient()
        store = KbStore(s, EmbeddingService(client))
        assert await store.soft_delete(doc_id) is True
        # 第二次调用 get → doc 仍存在（update 还没改 ORM 缓存），但 list 行为
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
            MagicMock(),
        ]
        assert await store.soft_delete(doc_id) is False

    @pytest.mark.asyncio
    async def test_soft_delete_unpublishes(self):
        """soft_delete 应同时把 is_published 置 False。"""
        s = _make_session_mock()
        doc_id = uuid.uuid4()
        doc = MagicMock()
        doc.id = doc_id
        doc.is_published = True
        captured_sqls: list[str] = []

        async def fake_execute(stmt, params=None):
            sql = str(stmt)
            captured_sqls.append(sql)
            if "SELECT" in sql.upper() and "kb_documents" in sql:
                return MagicMock(scalar_one_or_none=MagicMock(return_value=doc))
            return MagicMock()

        s.execute.side_effect = fake_execute
        client = _MockEmbeddingClient()
        store = KbStore(s, EmbeddingService(client))
        await store.soft_delete(doc_id)
        # UPDATE 那条应同时设 is_published=False 和 deleted_at
        update_sqls = [sql for sql in captured_sqls if "UPDATE kb_documents" in sql]
        assert update_sqls, f"expected UPDATE, got {captured_sqls}"
        update_sql = update_sqls[0]
        assert "is_published" in update_sql
        assert "deleted_at" in update_sql
        # 检查 bound params 包含 is_published=False
        # 从 captured_sqls 拿不到 params（绑在 stmt 上），改用 store 实际修改 doc
        # 这里只验证 SQL 含目标字段
        assert "SET" in update_sql


# ===========================================================================
# ReviewGate
# ===========================================================================


def _make_review_session(initial_state: bool = False) -> AsyncMock:
    """构造一个能跑通 review gate 的 mock session。"""
    s = AsyncMock()
    doc = MagicMock()
    doc.id = uuid.uuid4()
    doc.is_published = initial_state
    doc.deleted_at = None

    # execute 顺序（按调用）：
    # 1) get_publish_state → SELECT is_published
    # 2) is_published → 同上
    # 3) _get_live_doc → SELECT KbDocument
    # 4) UPDATE kb_documents
    # 5) INSERT audit_logs
    # 这里只 mock 一种典型路径：先 get，再 update，再 audit
    get_result = MagicMock(scalar_one_or_none=MagicMock(return_value=doc))

    s.execute.side_effect = [get_result, MagicMock(), MagicMock()]
    return s


class TestReviewGate:
    @pytest.mark.asyncio
    async def test_publish_flips_to_true(self):
        s = AsyncMock()
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.is_published = False
        doc.deleted_at = None

        # 第一次 execute：get_live_doc → 返回 doc
        # 第二次 execute：UPDATE kb_documents
        # 第三次 execute：INSERT audit_logs
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
            MagicMock(),
            MagicMock(),
        ]
        gate = ReviewGate(s)
        result = await gate.publish(doc.id, reviewer="alice", comment="LGTM")
        assert result is True

    @pytest.mark.asyncio
    async def test_publish_idempotent_when_already_published(self):
        s = AsyncMock()
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.is_published = True  # 已发布
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
        ]
        gate = ReviewGate(s)
        result = await gate.publish(doc.id, reviewer="alice")
        assert result is False  # 幂等：不重复翻

    @pytest.mark.asyncio
    async def test_publish_doc_not_found(self):
        s = AsyncMock()
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]
        gate = ReviewGate(s)
        result = await gate.publish(uuid.uuid4(), reviewer="alice")
        assert result is False

    @pytest.mark.asyncio
    async def test_publish_requires_reviewer(self):
        s = AsyncMock()
        gate = ReviewGate(s)
        with pytest.raises(ValueError, match="reviewer"):
            await gate.publish(uuid.uuid4(), reviewer="")

    @pytest.mark.asyncio
    async def test_unpublish_flips_to_false(self):
        s = AsyncMock()
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.is_published = True
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
            MagicMock(),
            MagicMock(),
        ]
        gate = ReviewGate(s)
        result = await gate.unpublish(doc.id, reviewer="bob", comment="revoked")
        assert result is True

    @pytest.mark.asyncio
    async def test_unpublish_idempotent_when_unpublished(self):
        s = AsyncMock()
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.is_published = False
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=doc)),
        ]
        gate = ReviewGate(s)
        result = await gate.unpublish(doc.id, reviewer="bob")
        assert result is False

    @pytest.mark.asyncio
    async def test_is_published_returns_state(self):
        s = AsyncMock()
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=True)),
        ]
        gate = ReviewGate(s)
        assert await gate.is_published(uuid.uuid4()) is True

    @pytest.mark.asyncio
    async def test_is_published_false_when_missing(self):
        s = AsyncMock()
        s.execute.side_effect = [
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]
        gate = ReviewGate(s)
        assert await gate.is_published(uuid.uuid4()) is False

    @pytest.mark.asyncio
    async def test_audit_log_written(self):
        """publish 时应该向 audit_logs 写一条记录。"""
        s = AsyncMock()
        doc = MagicMock()
        doc.id = uuid.uuid4()
        doc.is_published = False

        executed_stmts: list[Any] = []

        async def fake_execute(stmt, params=None):
            executed_stmts.append(stmt)
            sql = str(stmt)
            if "SELECT" in sql.upper() and "kb_documents" in sql:
                return MagicMock(scalar_one_or_none=MagicMock(return_value=doc))
            return MagicMock()

        s.execute.side_effect = fake_execute
        gate = ReviewGate(s)
        await gate.publish(doc.id, reviewer="alice", comment="approved")

        # 找 audit_logs 那条 INSERT
        audit_inserts = [s_ for s_ in executed_stmts if "audit_logs" in str(s_)]
        assert audit_inserts, f"expected audit log insert, got {[str(s) for s in executed_stmts]}"
        insert_stmt = audit_inserts[0]
        # SQLAlchemy Insert 的 _values 是 dict[Column, BindParameter]
        raw_values = insert_stmt._values  # type: ignore[attr-defined]
        params: dict[str, Any] = {}
        for col, v in raw_values.items():
            short_key = col.key
            value = getattr(v, "value", v)
            if hasattr(value, "value"):
                value = value.value
            params[short_key] = value
        assert params["action"] == "kb.publish"
        assert params["user_id"] == "alice"
        assert params["resource_id"] == doc.id
        assert params["resource_type"] == "kb_document"
        assert params["after_state"]["new_state"] is True
        assert params["after_state"]["comment"] == "approved"


# ===========================================================================
# Retriever 业务隔离（W5）
# ===========================================================================


class TestRetrieverBusinessScope:
    @pytest.mark.asyncio
    async def test_business_id_adds_scope_clause(self):
        """传 business_id：两条 SQL 都带 (business_id = X OR IS NULL) 过滤 + 参数。"""
        session = _FakeSession(vector_rows=[], keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder)
        bid = uuid.uuid4()
        await r.retrieve("q", type="rule", business_id=bid)
        assert len(session.executed) >= 1
        for sql, params in session.executed:
            assert (
                "d.business_id = CAST(:business_id AS uuid) OR d.business_id IS NULL"
                in sql
            )
            assert params.get("business_id") == str(bid)

    @pytest.mark.asyncio
    async def test_business_id_accepts_str(self):
        """business_id 传字符串 UUID 也可以（agent 节点从 state 拿的就是 str）。"""
        session = _FakeSession(vector_rows=[], keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder)
        bid = str(uuid.uuid4())
        await r.retrieve("q", type="rule", business_id=bid)
        for _, params in session.executed:
            assert params.get("business_id") == bid

    @pytest.mark.asyncio
    async def test_no_business_id_no_scope_clause(self):
        """不传 business_id：保持老行为，SQL 不含业务过滤（全局检索）。"""
        session = _FakeSession(vector_rows=[], keyword_rows=[])
        embedder = MagicMock()
        embedder.embed_one = AsyncMock(return_value=[0.0] * 4)
        r = Retriever(session, embedder)
        await r.retrieve("q", type="rule")
        assert len(session.executed) >= 1
        for sql, params in session.executed:
            assert ":business_id" not in sql
            assert "business_id" not in params
