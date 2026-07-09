"""LLM 客户端路由器：按模型名路由到对应 client。"""

from __future__ import annotations

from matrix.monitoring.logging import get_logger
import os
from typing import Any

from .clients import (
    MODEL_ALIASES,
    AnthropicClient,
    LLMClient,
    OpenAIClient,
    resolve_model,
)

logger = get_logger(__name__)


def _is_anthropic_model(model: str) -> bool:
    resolved = resolve_model(model)
    return resolved.startswith("claude-")


def _is_openai_model(model: str) -> bool:
    resolved = resolve_model(model)
    return resolved.startswith("gpt-") or resolved.startswith("o")


_client_cache: dict[str, LLMClient] = {}


def get_client(model_name: str, *, force_new: bool = False, **kwargs: Any) -> LLMClient:
    """根据模型名返回对应 client 实例（进程内缓存）。

    Args:
        model_name: 模型名或别名（如 ``sonnet`` / ``gpt-5`` / ``claude-haiku-4-5``）
        force_new: 强制创建新实例（默认进程内单例）
        **kwargs: 透传给 client 构造函数（如 api_key）
    """
    resolved = resolve_model(model_name)
    cache_key = f"{_provider_for(resolved)}:{id(kwargs.get('api_key'))}"

    if not force_new and cache_key in _client_cache:
        return _client_cache[cache_key]

    if _is_anthropic_model(resolved):
        client: LLMClient = AnthropicClient(**kwargs)
    elif _is_openai_model(resolved):
        client = OpenAIClient(**kwargs)
    else:
        raise ValueError(
            f"unknown model: {model_name!r} (resolved={resolved!r}); "
            f"add it to MODEL_ALIASES or extend the router"
        )

    _client_cache[cache_key] = client
    logger.debug(
        "llm.router.get_client",
        model=model_name,
        provider=client.provider,
    )
    return client


def _provider_for(model: str) -> str:
    if model.startswith("claude-"):
        return "anthropic"
    if model.startswith("gpt-") or model.startswith("o"):
        return "openai"
    return "unknown"


def get_default_client() -> LLMClient:
    """根据环境变量选择默认 client。

    - ``MATRIX_LLM_PROVIDER=anthropic`` → AnthropicClient
    - ``MATRIX_LLM_PROVIDER=openai`` → OpenAIClient
    - 未设置：优先 anthropic（兼容现有调用）
    """
    provider = os.environ.get("MATRIX_LLM_PROVIDER", "anthropic").lower()
    if provider == "anthropic":
        return AnthropicClient()
    if provider == "openai":
        return OpenAIClient()
    raise ValueError(f"unsupported MATRIX_LLM_PROVIDER: {provider}")


def reset_client_cache() -> None:
    """清空客户端缓存（测试用）。"""
    _client_cache.clear()
