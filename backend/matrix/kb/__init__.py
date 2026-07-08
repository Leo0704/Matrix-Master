"""知识库子系统（RAG）。

主要导出：

- :class:`Chunk` / :class:`Chunker` — 文本切分
- :class:`EmbeddingService` — 带缓存 + 批量切批的 embedding 包装
- :class:`Retriever` / :class:`ChunkResult` — 混合检索（向量 + 关键词 + RRF）
- :class:`KbStore` — 文档 / chunk 的 CRUD
- :class:`ReviewGate` — 文档发布门禁
"""
from __future__ import annotations

from .chunker import Chunk, Chunker
from .embedding import EmbeddingService
from .promotion import ReviewGate
from .retrieval import (
    DEFAULT_RRF_K,
    META_FILTERABLE_KEYS,
    ChunkResult,
    Retriever,
)
from .store import KB_TYPES, KbStore

__all__ = [
    # chunker
    "Chunk",
    "Chunker",
    # embedding
    "EmbeddingService",
    # retrieval
    "ChunkResult",
    "Retriever",
    "DEFAULT_RRF_K",
    "META_FILTERABLE_KEYS",
    # store
    "KbStore",
    "KB_TYPES",
    # review
    "ReviewGate",
]
