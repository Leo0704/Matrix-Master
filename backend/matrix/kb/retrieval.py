"""Hybrid retrieval：向量 + 关键词 + metadata filter，RRF 重排序。

策略（与 SDD §3.2.3 一致）：
- 向量：pgvector cosine 距离（``kb_chunks`` / ``kb_documents``）
- 关键词：PostgreSQL ``ts_vector`` + ``to_tsquery``
- 合并：Reciprocal Rank Fusion（k 默认 60）
- 过滤：metadata filter 作为 ``WHERE`` 条件 AND 到两条查询里

实现要点：
- 用纯文本 SQL + 参数化查询，便于测试用 mock session 验证
- 公开 ``async retrieve`` 接口，签名与 SDD 一致
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession



logger = get_logger(__name__)

DEFAULT_RRF_K: int = 60
DEFAULT_CANDIDATE_MULTIPLIER: int = 2  # 候选 top_k * multiplier


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkResult:
    """检索结果中的单条 chunk。"""

    chunk_id: uuid.UUID
    doc_id: uuid.UUID
    doc_type: str
    doc_title: Optional[str]
    chunk_index: int
    text: str
    score: float
    # 命中的信号：哪个检索器贡献了这条结果
    sources: tuple[str, ...] = field(default_factory=tuple)
    # 该 chunk 所属 document 的 metadata，便于上层做去重 / 排序 / 调试
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Embedder protocol — 让 Retriever 接受任何有 ``embed_one`` / ``embed_batch`` 的对象
# 实际生产用 ``EmbeddingService``；测试可以传 mock
# ---------------------------------------------------------------------------


class EmbedderLike(Protocol):
    async def embed_one(self, text: str, *, model: Optional[str] = None) -> list[float]: ...
    async def embed_batch(
        self, texts: list[str], *, model: Optional[str] = None
    ) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# ts_query helper
# ---------------------------------------------------------------------------


# 词法清洗：把任意查询字符串转成 ``to_tsquery`` 能接受的 ``'word1' & 'word2' & ...`` 形式
_TOKEN_RE = re.compile(r"[A-Za-z0-9_一-鿿]+")


def _build_ts_query(query: str) -> Optional[str]:
    """从自由文本构造 ``to_tsquery`` 表达式。

    - 抽取出「合法 token」
    - 多 token 用 ``&`` 拼接
    - 全部清洗后没 token 则返回 ``None``（调用方跳过关键词检索）
    """
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        return None
    # 全部转小写后再用 single-quote 包裹；' 需替换为 ''
    escaped = [t.replace("'", "''").lower() for t in tokens]
    return " & ".join(f"'{t}'" for t in escaped)


# ---------------------------------------------------------------------------
# Metadata filter
# ---------------------------------------------------------------------------


# metadata 字段的允许过滤 key（白名单，防止任意 JSONB key 注入）
# - 原有：severity / category / source / tags / account_id
# - 商品库扩展：price / size / style / category / product_sku
META_FILTERABLE_KEYS: frozenset[str] = frozenset(
    {
        "severity",
        "category",
        "source",
        "tags",
        "account_id",
        # 商品事实库字段
        "price",
        "size",
        "style",
        "product_sku",
    }
)


def _build_metadata_clause(filters: dict) -> tuple[str, dict]:
    """把 metadata filter 翻译成 ``(sql_clause, params)``。

    支持：
    - 顶层标量 ``filters['severity'] = 5`` → ``(metadata->>'severity')::int = :meta_severity``
    - 顶层数组 ``filters['tags'] = ['美妆']`` → ``metadata->'tags' ?| array[:meta_tags]``
    - 类型自动从值推断
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    for i, (key, val) in enumerate(filters.items()):
        if key not in META_FILTERABLE_KEYS:
            raise ValueError(f"filter key not allowed: {key!r}")
        p = f"meta_{key}_{i}"
        if isinstance(val, bool):
            clauses.append(f"(metadata->>{key!r})::boolean = :{p}")
            params[p] = val
        elif isinstance(val, (int, float)):
            clauses.append(f"(metadata->>{key!r})::numeric = :{p}")
            params[p] = val
        elif isinstance(val, str):
            clauses.append(f"metadata->>{key!r} = :{p}")
            params[p] = val
        elif isinstance(val, list):
            # 数组包含任意 → 用 ``?|``（overlap）
            clauses.append(f"metadata->{key!r} ?| :{p}")
            params[p] = [str(v) for v in val]
        else:
            raise TypeError(f"unsupported filter value type: {key!r}={type(val)}")
    return " AND ".join(clauses), params


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


class Retriever:
    """混合检索器。"""

    def __init__(
        self,
        session: AsyncSession,
        embedder: EmbedderLike,
        *,
        rrf_k: int = DEFAULT_RRF_K,
        candidate_multiplier: int = DEFAULT_CANDIDATE_MULTIPLIER,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError(f"rrf_k must be > 0, got {rrf_k}")
        if candidate_multiplier < 1:
            raise ValueError(f"candidate_multiplier must be >= 1, got {candidate_multiplier}")
        self._session = session
        self._embedder = embedder
        self._rrf_k = rrf_k
        self._candidate_multiplier = candidate_multiplier

    @property
    def rrf_k(self) -> int:
        return self._rrf_k

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def retrieve(
        self,
        query: str,
        type: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
    ) -> list[ChunkResult]:
        """混合检索。

        Args:
            query: 用户查询
            type: ``kb_documents.type`` 过滤（``brand`` / ``persona`` / ``rule`` / ...）
            top_k: 返回结果数
            filters: metadata 过滤（见 ``META_FILTERABLE_KEYS``）

        Returns:
            按 RRF 分数降序的 ``ChunkResult`` 列表，长度 <= ``top_k``
        """
        if not query.strip():
            return []
        if top_k <= 0:
            return []
        filters = filters or {}

        candidate_k = top_k * self._candidate_multiplier

        query_vec = await self._embedder.embed_one(query)

        meta_clause, meta_params = _build_metadata_clause(filters)
        common_where = [
            "d.deleted_at IS NULL",
            "d.is_published = TRUE",
            "d.type = :doc_type",
        ]
        if meta_clause:
            common_where.append(meta_clause)
        common_where_sql = " AND ".join(common_where)
        common_params: dict[str, Any] = {
            "doc_type": type,
            "query_vec_str": _vec_to_pgvector_str(query_vec),
            **meta_params,
        }

        # 1) 向量检索（kb_chunks 表的 embedding）
        vector_rows = await self._vector_search(common_where_sql, common_params, candidate_k)
        # 2) 关键词检索（ts_vector）
        keyword_rows = await self._keyword_search(
            query, common_where_sql, common_params, candidate_k
        )

        # 3) RRF 融合
        results = _rrf_fuse(
            vector_results=vector_rows,
            keyword_results=keyword_rows,
            rrf_k=self._rrf_k,
            top_k=top_k,
        )
        return results

    # ------------------------------------------------------------------
    # private helpers
    # ------------------------------------------------------------------

    async def _vector_search(
        self, where_sql: str, params: dict, limit: int
    ) -> list[_RawHit]:
        """用 pgvector cosine 距离在 ``kb_chunks`` 上做近似最近邻。"""
        sql = sa_text(
            f"""
            SELECT
                c.id           AS chunk_id,
                c.doc_id       AS doc_id,
                d.type         AS doc_type,
                d.title        AS doc_title,
                c.chunk_index  AS chunk_index,
                c.text         AS text,
                d.metadata     AS metadata,
                1 - (c.embedding <=> CAST(:query_vec_str AS vector)) AS distance
            FROM kb_chunks c
            JOIN kb_documents d ON d.id = c.doc_id
            WHERE {where_sql}
            ORDER BY c.embedding <=> CAST(:query_vec_str AS vector)
            LIMIT :limit
            """
        )
        rows = await self._session.execute(sql, {**params, "limit": limit})
        return [
            _RawHit(
                chunk_id=row.chunk_id,
                doc_id=row.doc_id,
                doc_type=row.doc_type,
                doc_title=row.doc_title,
                chunk_index=row.chunk_index,
                text=row.text,
                metadata=dict(row.metadata or {}),
                distance=float(row.distance),
            )
            for row in rows
        ]

    async def _keyword_search(
        self,
        query: str,
        where_sql: str,
        params: dict,
        limit: int,
    ) -> list[_RawHit]:
        """用 ts_vector 在 ``kb_chunks`` 上做全文检索。"""
        ts_query = _build_ts_query(query)
        if ts_query is None:
            return []

        sql = sa_text(
            f"""
            SELECT
                c.id           AS chunk_id,
                c.doc_id       AS doc_id,
                d.type         AS doc_type,
                d.title        AS doc_title,
                c.chunk_index  AS chunk_index,
                c.text         AS text,
                d.metadata     AS metadata,
                ts_rank(to_tsvector('simple', c.text), to_tsquery('simple', :ts_query)) AS rank
            FROM kb_chunks c
            JOIN kb_documents d ON d.id = c.doc_id
            WHERE
                {where_sql}
                AND to_tsvector('simple', c.text) @@ to_tsquery('simple', :ts_query)
            ORDER BY rank DESC
            LIMIT :limit
            """
        )
        rows = await self._session.execute(
            sql, {**params, "ts_query": ts_query, "limit": limit}
        )
        return [
            _RawHit(
                chunk_id=row.chunk_id,
                doc_id=row.doc_id,
                doc_type=row.doc_type,
                doc_title=row.doc_title,
                chunk_index=row.chunk_index,
                text=row.text,
                metadata=dict(row.metadata or {}),
                distance=float(row.rank),  # 占位：实际语义是「rank score」
            )
            for row in rows
        ]


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


@dataclass
class _RawHit:
    chunk_id: uuid.UUID
    doc_id: uuid.UUID
    doc_type: str
    doc_title: Optional[str]
    chunk_index: int
    text: str
    metadata: dict
    distance: float  # 向量检索用 cosine distance；关键词检索用 rank score


def _rrf_fuse(
    *,
    vector_results: list[_RawHit],
    keyword_results: list[_RawHit],
    rrf_k: int,
    top_k: int,
) -> list[ChunkResult]:
    """Reciprocal Rank Fusion。

    score(d) = sum_{r in retrievers} 1 / (rrf_k + rank_r(d))

    - rank 从 1 开始
    - 同一 chunk 在两个检索器中都出现：分数累加；``sources`` 字段会标注两路
    - 同一 doc 的不同 chunk 视为不同结果（保留 chunk 级别粒度）
    """
    if top_k <= 0:
        return []

    # chunk_id -> (ChunkResult, score)
    scoreboard: dict[uuid.UUID, tuple[ChunkResult, float]] = {}

    def add(rank: int, hit: _RawHit, source: str) -> None:
        contribution = 1.0 / (rrf_k + rank)
        existing = scoreboard.get(hit.chunk_id)
        if existing is None:
            cr = ChunkResult(
                chunk_id=hit.chunk_id,
                doc_id=hit.doc_id,
                doc_type=hit.doc_type,
                doc_title=hit.doc_title,
                chunk_index=hit.chunk_index,
                text=hit.text,
                score=contribution,
                sources=(source,),
                metadata=hit.metadata,
            )
            scoreboard[hit.chunk_id] = (cr, contribution)
        else:
            cr, prev = existing
            new_score = prev + contribution
            new_sources = cr.sources + (source,) if source not in cr.sources else cr.sources
            scoreboard[hit.chunk_id] = (
                ChunkResult(
                    chunk_id=cr.chunk_id,
                    doc_id=cr.doc_id,
                    doc_type=cr.doc_type,
                    doc_title=cr.doc_title,
                    chunk_index=cr.chunk_index,
                    text=cr.text,
                    score=new_score,
                    sources=new_sources,
                    metadata=cr.metadata,
                ),
                new_score,
            )

    for rank, hit in enumerate(vector_results, start=1):
        add(rank, hit, "vector")
    for rank, hit in enumerate(keyword_results, start=1):
        add(rank, hit, "keyword")

    # 按 RRF 分数降序、然后按 chunk_index 升序、最后按 chunk_id 升序（保证测试稳定）
    merged = [cr for cr, _ in scoreboard.values()]
    merged.sort(
        key=lambda r: (-r.score, r.chunk_index, str(r.chunk_id))
    )
    return merged[:top_k]


# ---------------------------------------------------------------------------
# vector serialization
# ---------------------------------------------------------------------------


def _vec_to_pgvector_str(vec: list[float]) -> str:
    """把 Python 列表转成 pgvector 字面量：``[v1,v2,...]``。"""
    # NaN / Inf 必须剔除（pgvector 不接受）
    safe = [0.0 if (not math.isfinite(v)) else float(v) for v in vec]
    return "[" + ",".join(repr(v) for v in safe) + "]"


__all__ = [
    "ChunkResult",
    "DEFAULT_RRF_K",
    "META_FILTERABLE_KEYS",
    "Retriever",
    "build_ts_query",
]


# 兼容导出（部分调用方可能按这个名字找）
build_ts_query = _build_ts_query
