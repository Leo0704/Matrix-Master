"""Embedding service：包装 ``EmbeddingClient``，加缓存和批量切批。

约束（与 SDD §3.2.2 一致）：
- 缓存键：``(text_hash, model)`` → ``list[float]``
- 内部按 100 切批（与 OpenAI 推荐的 batch size 一致）
- 仅依赖 ``matrix.llm.embeddings.EmbeddingClient``，不直接调 SDK
"""
from __future__ import annotations

import hashlib
from matrix.monitoring.logging import get_logger
from collections import OrderedDict
from typing import Optional

from matrix.llm.embeddings import EmbeddingClient


logger = get_logger(__name__)

DEFAULT_BATCH_SIZE: int = 100
DEFAULT_CACHE_SIZE: int = 4096


def _hash_text(text: str) -> str:
    """文本的稳定哈希。SHA-256 截前 32 hex 字符即可。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class EmbeddingService:
    """带缓存 + 批量切批的 embedding 服务。

    Args:
        client: ``EmbeddingClient`` 实例
        default_model: 默认 embedding 模型
        batch_size: 单次 API 调用的最大批大小
        cache_size: LRU 缓存容量（按 (hash, model) 计数）
    """

    def __init__(
        self,
        client: EmbeddingClient,
        *,
        default_model: str = "BAAI/bge-m3",
        batch_size: int = DEFAULT_BATCH_SIZE,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        if batch_size <= 0:
            raise ValueError(f"batch_size must be > 0, got {batch_size}")
        if cache_size < 0:
            raise ValueError(f"cache_size must be >= 0, got {cache_size}")
        self._client = client
        self._default_model = default_model
        self._batch_size = batch_size
        # OrderedDict 实现 LRU（move_to_end + popitem）
        self._cache: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
        self._cache_size = cache_size

    @property
    def default_model(self) -> str:
        return self._default_model

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        """清空 LRU 缓存。"""
        self._cache.clear()

    def _cache_get(self, key: tuple[str, str]) -> Optional[list[float]]:
        if self._cache_size == 0:
            return None
        try:
            value = self._cache[key]
        except KeyError:
            return None
        # 命中：挪到末尾（标记最近使用）
        self._cache.move_to_end(key)
        return value

    def _cache_put(self, key: tuple[str, str], value: list[float]) -> None:
        if self._cache_size == 0:
            return
        if key in self._cache:
            self._cache.move_to_end(key)
            self._cache[key] = value
            return
        self._cache[key] = value
        if len(self._cache) > self._cache_size:
            # 弹出最久未使用
            self._cache.popitem(last=False)

    async def embed_one(self, text: str, *, model: Optional[str] = None) -> list[float]:
        """单条文本 embedding（内部走批量路径以复用缓存 / 切批逻辑）。"""
        results = await self.embed_batch([text], model=model)
        return results[0]

    async def embed_batch(
        self,
        texts: list[str],
        *,
        model: Optional[str] = None,
    ) -> list[list[float]]:
        """批量 embedding。

        - 按 (text_hash, model) 命中缓存
        - 未命中的按 ``batch_size`` 切批调 API
        - 返回与 ``texts`` 等长的向量列表
        """
        if not texts:
            return []

        use_model = model or self._default_model
        results: list[Optional[list[float]]] = [None] * len(texts)

        # 1) 缓存查找
        to_fetch: list[tuple[int, str]] = []  # (original_index, text)
        for i, text in enumerate(texts):
            key = (_hash_text(text), use_model)
            cached = self._cache_get(key)
            if cached is not None:
                results[i] = cached
            else:
                to_fetch.append((i, text))

        if not to_fetch:
            return [r for r in results if r is not None]  # type: ignore[misc]

        # 2) 未命中按 batch_size 切批
        for start in range(0, len(to_fetch), self._batch_size):
            batch = to_fetch[start : start + self._batch_size]
            batch_texts = [t for _, t in batch]
            vectors = await self._client.embed(batch_texts, model=use_model)
            for (orig_idx, text), vec in zip(batch, vectors):
                results[orig_idx] = vec
                self._cache_put((_hash_text(text), use_model), vec)

        # 全部填满，返回
        return [r for r in results if r is not None]  # type: ignore[misc]
