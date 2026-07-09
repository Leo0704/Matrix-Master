"""KB embedding 服务单例。

EmbeddingClient 内部持有异步 HTTP 客户端；每次请求重建会重复 TCP/TLS 握手。
这里按客户端类名缓存 EmbeddingService，保留测试需要的强制重建入口。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from matrix.kb.embedding import EmbeddingService
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

_embedder_singleton: dict[str, EmbeddingService] = {}
_lock = asyncio.Lock()


async def get_embedder(
    embedding_cls: Callable[..., Any],
    *,
    force_new: bool = False,
    **kwargs: Any,
) -> EmbeddingService:
    """返回按 embedding client 类名缓存的 EmbeddingService。"""
    key = getattr(embedding_cls, "__name__", embedding_cls.__class__.__name__)
    if not force_new:
        cached = _embedder_singleton.get(key)
        if cached is not None:
            return cached

    async with _lock:
        if not force_new:
            cached = _embedder_singleton.get(key)
            if cached is not None:
                return cached
        try:
            embedder = EmbeddingService(embedding_cls(**kwargs))
        except Exception:
            logger.exception("kb.embedder_singleton.create_failed cls=%s", key)
            raise
        _embedder_singleton[key] = embedder
        return embedder


__all__ = ["get_embedder"]
