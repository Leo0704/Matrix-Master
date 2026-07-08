"""Embedding 客户端封装。

支持模型：
- ``text-embedding-3-small`` → 1536 维
- ``text-embedding-3-large`` → 3072 维
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .errors import LLMError

logger = logging.getLogger(__name__)


# 模型 → 维度
EMBEDDING_DIMENSIONS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}


def get_embedding_dimensions(model: str) -> int:
    if model not in EMBEDDING_DIMENSIONS:
        raise LLMError(
            f"unknown embedding model: {model}",
            provider="openai",
            model=model,
        )
    return EMBEDDING_DIMENSIONS[model]


class EmbeddingClient:
    """OpenAI Embedding 客户端。"""

    def __init__(self, *, api_key: str | None = None, default_model: str = "text-embedding-3-small", **kwargs: Any) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, **kwargs)
        self._default_model = default_model

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        timeout: float = 10.0,
    ) -> list[list[float]]:
        """批量 embedding。返回与 texts 等长的向量列表。"""
        if not texts:
            return []

        use_model = model or self._default_model
        if use_model not in EMBEDDING_DIMENSIONS:
            raise LLMError(
                f"unsupported embedding model: {use_model}",
                provider="openai",
                model=use_model,
            )

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.embeddings.create(model=use_model, input=texts),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            # 复用 openai 异常映射
            from .clients import _map_openai_error

            raise _map_openai_error(exc) from exc
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.debug(
            "embedding.batch size=%d model=%s latency_ms=%d",
            len(texts),
            use_model,
            latency_ms,
        )

        # 按 index 排序以保证返回顺序
        data = sorted(response.data, key=lambda x: x.index)
        return [list(item.embedding) for item in data]
