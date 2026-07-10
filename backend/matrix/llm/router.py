from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from matrix.config import get_settings
from matrix.monitoring.logging import get_logger

from .clients import (
    AnthropicClient,
    LLMClient,
    OpenAIClient,
    resolve_model,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider 表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderSpec:
    """provider 协议元数据。

    protocol="openai" 时复用 ``OpenAIClient``；"anthropic" 时用 ``AnthropicClient``。
    ``base_url=None`` 表示用 SDK 默认（直连官方）。
    """

    protocol: Literal["anthropic", "openai"]
    base_url: str | None
    api_key_env: str
    label: str  # 显示用


# key 是用于匹配的 model 前缀（最长前缀优先）
PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        protocol="anthropic",
        base_url=None,
        api_key_env="ANTHROPIC_API_KEY",
        label="Anthropic Claude",
    ),
    "openai": ProviderSpec(
        protocol="openai",
        base_url=None,
        api_key_env="OPENAI_API_KEY",
        label="OpenAI",
    ),
    # 国内 model 走 OpenAI 兼容协议，复用 OpenAIClient + 注入 base_url
    "deepseek": ProviderSpec(
        protocol="openai",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        label="DeepSeek",
    ),
    "tongyi": ProviderSpec(
        protocol="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env="DASHSCOPE_API_KEY",
        label="通义千问 (DashScope 兼容模式)",
    ),
    "glm": ProviderSpec(
        protocol="openai",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key_env="ZHIPUAI_API_KEY",
        label="智谱 GLM",
    ),
    "doubao": ProviderSpec(
        protocol="openai",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env="DOUBAO_API_KEY",
        label="豆包 Seedance",
    ),
    "minimax": ProviderSpec(
        protocol="anthropic",
        base_url="https://api.minimaxi.com/anthropic",
        api_key_env="MINIMAX_API_KEY",
        label="MiniMax M3 (Anthropic 兼容)",
    ),
}

# 匹配用前缀：model 命名约定
# 1. 显式前缀 deepseek-/tongyi-/glm-/doubao- 优先匹配
# 2. claude- 走 anthropic
# 3. gpt-/o* 走 openai
_PREFIX_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("deepseek-", "deepseek"),
    ("qwen-", "tongyi"),
    ("glm-", "glm"),
    ("doubao-", "doubao"),
    ("MiniMax-", "minimax"),
    ("claude-", "anthropic"),
)


def _provider_for_model(model: str) -> str:
    """按前缀匹配 provider。"""
    for prefix, name in _PREFIX_PROVIDERS:
        if model.startswith(prefix):
            return name
    if model.startswith("gpt-") or model.startswith("o"):
        return "openai"
    raise ValueError(
        f"unknown model provider: {model!r}; "
        f"add it to PROVIDERS or use a known prefix"
    )


# ---------------------------------------------------------------------------
# Client 缓存 + 工厂
# ---------------------------------------------------------------------------


_client_cache: dict[str, LLMClient] = {}


def _build_client(provider_name: str, *, base_url: str | None, **kwargs: Any) -> LLMClient:
    spec = PROVIDERS[provider_name]
    settings = get_settings()
    # 自动注入 base_url（如果调用方没传）+ api_key
    if spec.protocol == "anthropic":
        if spec.base_url and "base_url" not in kwargs:
            kwargs["base_url"] = spec.base_url
        if "api_key" not in kwargs:
            kwargs["api_key"] = _api_key_from_settings(spec.api_key_env, settings)
        return AnthropicClient(**kwargs)
    # openai 兼容（含国产 4 家）
    if spec.base_url and "base_url" not in kwargs:
        kwargs["base_url"] = spec.base_url
    if "api_key" not in kwargs:
        env_val = _api_key_from_settings(spec.api_key_env, settings)
        if env_val:
            kwargs["api_key"] = env_val
    return OpenAIClient(**kwargs)


def _api_key_from_settings(env_name: str, settings: Any) -> str | None:
    """从 ``Settings`` 取 api_key（env 名 → 字段名大小写不敏感映射）。"""
    field_name = env_name.lower()
    return getattr(settings, field_name, None)


def get_client(model_name: str, *, force_new: bool = False, **kwargs: Any) -> LLMClient:
    """根据模型名返回对应 client 实例（进程内缓存）。

    Args:
        model_name: 模型名或别名
        force_new: 强制创建新实例（默认进程内单例）
        **kwargs: 透传给 client 构造函数（api_key / base_url 等）

    缓存键：``(provider, base_url, id(api_key))``
    """
    resolved = resolve_model(model_name)
    provider_name = _provider_for_model(resolved)
    spec = PROVIDERS[provider_name]
    settings = get_settings()

    base_url = kwargs.get("base_url") or spec.base_url
    api_key = kwargs.get("api_key") or _api_key_from_settings(spec.api_key_env, settings) or ""
    cache_key = f"{spec.protocol}:{provider_name}:{base_url}:{id(api_key)}"

    if not force_new and cache_key in _client_cache:
        return _client_cache[cache_key]

    client = _build_client(
        provider_name, base_url=kwargs.get("base_url"), **kwargs
    )
    _client_cache[cache_key] = client
    logger.debug(
        "llm.router.get_client model=%s provider=%s base_url=%s",
        model_name,
        provider_name,
        base_url,
    )
    return client


def get_default_client() -> LLMClient:
    """按 ``MATRIX_LLM_PROVIDER`` env 选默认 client；未设置走 ``tongyi`` (v0.7 默认)。

    也可用 ``MATRIX_LLM_MODEL`` 选默认 model。
    """
    settings = get_settings()
    provider = (settings.matrix_llm_provider or "tongyi").lower()
    if provider not in PROVIDERS:
        raise ValueError(
            f"unsupported MATRIX_LLM_PROVIDER: {provider!r}; "
            f"choose from {sorted(PROVIDERS)}"
        )
    model = settings.matrix_llm_model or _default_model_for(provider)
    return get_client(model)


def _default_model_for(provider: str) -> str:
    return {
        "anthropic": "sonnet",
        "openai": "gpt-5",
        "deepseek": "deepseek-chat",
        "tongyi": "qwen-plus",
        "glm": "glm-4-plus",
        "doubao": "doubao-pro-32k",
        "minimax": "MiniMax-M3",
    }.get(provider, "qwen-plus")


def reset_client_cache() -> None:
    """清空客户端缓存（测试用）。"""
    _client_cache.clear()


__all__ = [
    "PROVIDERS",
    "ProviderSpec",
    "get_client",
    "get_default_client",
    "reset_client_cache",
    "_provider_for_model",
]
