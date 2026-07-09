"""LLM 客户端封装。

主要导出：

- :class:`LLMClient` / :class:`AnthropicClient` / :class:`OpenAIClient`
- :class:`EmbeddingClient`
- :class:`CompletionCache` 缓存 + :func:`retry_with_backoff` 重试
- :class:`UsageTracker` / :class:`InMemoryUsageTracker` / :class:`UsageRecord` 用量
- :func:`get_client` 路由器
- 异常：:class:`LLMError` / :class:`RateLimitError` / :class:`LLMTimeoutError` /
  :class:`AuthError` / :class:`InvalidRequestError`
"""

from __future__ import annotations

from .cache import CompletionCache
from .clients import (
    MODEL_ALIASES,
    PRICING,
    AnthropicClient,
    CompletionResult,
    LLMClient,
    OpenAIClient,
    calculate_cost_usd,
    resolve_model,
)
from .embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingClient,
    get_embedding_dimensions,
)
from .errors import (
    AuthError,
    InvalidRequestError,
    LLMError,
    LLMTimeoutError,
    RateLimitError,
)
from .image_gen import (  # v0.7 Phase 3
    DoubaoSeedreamClient,
    ImageGenClient,
    ImageGenError,
    ImageGenResult,
    InMemoryImageGenClient,
    TongyiWanxiangClient,
    ZhipuCogViewClient,
    get_image_gen_client,
)
from .prompt_caching import CachedBlock, CachedMessages
from .retry import retry_with_backoff
from .router import get_client, get_default_client, reset_client_cache
from .usage import InMemoryUsageTracker, UsageRecord, UsageTracker

__all__ = [
    # clients
    "LLMClient",
    "AnthropicClient",
    "OpenAIClient",
    "CompletionResult",
    "MODEL_ALIASES",
    "PRICING",
    "resolve_model",
    "calculate_cost_usd",
    # embeddings
    "EmbeddingClient",
    "EMBEDDING_DIMENSIONS",
    "get_embedding_dimensions",
    # cache
    "CompletionCache",
    # retry
    "retry_with_backoff",
    # usage
    "UsageTracker",
    "InMemoryUsageTracker",
    "UsageRecord",
    # router
    "get_client",
    "get_default_client",
    "reset_client_cache",
    # prompt caching
    "CachedBlock",
    "CachedMessages",
    # errors
    "LLMError",
    "RateLimitError",
    "LLMTimeoutError",
    "AuthError",
    "InvalidRequestError",
    # image gen (v0.7)
    "ImageGenClient",
    "ImageGenResult",
    "ImageGenError",
    "InMemoryImageGenClient",
    "TongyiWanxiangClient",
    "ZhipuCogViewClient",
    "DoubaoSeedreamClient",
    "get_image_gen_client",
]
