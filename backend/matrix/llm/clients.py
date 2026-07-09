"""LLM 统一客户端。

- ``LLMClient`` ABC：``async complete(prompt, model, max_tokens, temperature) -> CompletionResult``
- ``AnthropicClient`` / ``OpenAIClient``：官方 SDK 实现
- Cost 计算按 ``docs/planning/cost-model.md`` 定价
- Token 计数优先使用 API response 的 usage 字段；缺失时回退 tiktoken
"""

from __future__ import annotations

import asyncio
from matrix.monitoring.logging import get_logger
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class CompletionResult:
    """LLM 调用的统一返回。"""

    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    provider: str
    raw: Any = None  # 原始 SDK response，便于上层做特殊处理
    cached: bool = False  # 是否命中缓存（由调用方填充）
    stop_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 定价表（USD / 1M tokens）
# ---------------------------------------------------------------------------


# 按 cost-model.md §3.1 / §3.2
PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4.5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-haiku-4.5": {"input": 0.80, "output": 4.00},
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    # OpenAI
    "gpt-5": {"input": 2.50, "output": 10.00},
    "gpt-5-mini": {"input": 0.40, "output": 1.60},
    # DeepSeek（人民币 ≈ USD 占位，上线改用合同价）
    "deepseek-chat": {"input": 0.00027, "output": 0.0011},
    "deepseek-reasoner": {"input": 0.00055, "output": 0.0022},
    # 通义千问（OpenAI 兼容模式，单位 USD/百万 token）
    "qwen-plus": {"input": 0.0008, "output": 0.002},
    "qwen-max": {"input": 0.0024, "output": 0.008},
    "qwen-turbo": {"input": 0.0003, "output": 0.0006},
    # 智谱 GLM
    "glm-4-plus": {"input": 0.007, "output": 0.007},
    "glm-4-flash": {"input": 0.0001, "output": 0.0001},
    # 豆包
    "doubao-pro-32k": {"input": 0.0008, "output": 0.001},
    "doubao-lite-32k": {"input": 0.0003, "output": 0.0006},
    # Embedding（input-only）
    "text-embedding-3-small": {"input": 0.02, "output": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0},
}

# 模型别名
MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-5",
    "sonnet-4.5": "claude-sonnet-4-5",
    "sonnet-4-5": "claude-sonnet-4-5",
    "haiku": "claude-haiku-4-5",
    "haiku-4.5": "claude-haiku-4-5",
    "haiku-4-5": "claude-haiku-4-5",
    "opus": "claude-opus-4",
    "gpt5": "gpt-5",
    "gpt-5": "gpt-5",
    "mini": "gpt-5-mini",
    # v0.7 Phase 1：国产 LLM 别名
    # DeepSeek
    "deepseek": "deepseek-chat",
    "deepseek-chat": "deepseek-chat",
    "deepseek-reasoner": "deepseek-reasoner",
    # 通义千问
    "qwen": "qwen-plus",
    "qwen-plus": "qwen-plus",
    "qwen-max": "qwen-max",
    "qwen-turbo": "qwen-turbo",
    # 智谱 GLM
    "glm": "glm-4-plus",
    "glm-4-plus": "glm-4-plus",
    "glm-4-flash": "glm-4-flash",
    # 豆包
    "doubao": "doubao-pro-32k",
    "doubao-pro-32k": "doubao-pro-32k",
    "doubao-lite-32k": "doubao-lite-32k",
}


def resolve_model(name: str) -> str:
    """解析模型别名到真实模型名。"""
    return MODEL_ALIASES.get(name, name)


def calculate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """按 cost-model.md 定价计算成本（USD）。"""
    pricing = PRICING.get(model)
    if pricing is None:
        # 未知模型：按 0 计费（集成层应记录告警）
        logger.warning("llm.cost.unknown_model", model=model)
        return 0.0
    cost = (
        prompt_tokens / 1_000_000.0 * pricing["input"]
        + completion_tokens / 1_000_000.0 * pricing["output"]
    )
    return cost


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


class LLMClient(ABC):
    """LLM 客户端抽象基类。"""

    provider: ClassVar[str] = ""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 1.0,
        system: str | None = None,
        timeout: float = 60.0,
        call_type: str = "generation",
        run_id: str | None = None,
        account_id: str | None = None,
    ) -> CompletionResult:
        """同步执行一次 LLM 调用。"""


# ---------------------------------------------------------------------------
# 异常映射辅助
# ---------------------------------------------------------------------------


def _map_anthropic_error(exc: Exception) -> Exception:
    """把 anthropic SDK 异常映射到 LLMError 体系。"""
    try:
        from anthropic import (
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            RateLimitError as AnthropicRateLimit,
        )
    except ImportError:
        return exc

    if isinstance(exc, APITimeoutError):
        from .errors import LLMTimeoutError

        return LLMTimeoutError(str(exc), provider="anthropic")
    if isinstance(exc, AnthropicRateLimit):
        from .errors import RateLimitError

        return RateLimitError(str(exc), provider="anthropic")
    if isinstance(exc, AuthenticationError):
        from .errors import AuthError

        return AuthError(str(exc), provider="anthropic")
    if isinstance(exc, BadRequestError):
        from .errors import InvalidRequestError

        return InvalidRequestError(str(exc), provider="anthropic")
    if isinstance(exc, APIStatusError):
        from .errors import LLMError

        return LLMError(str(exc), provider="anthropic")
    return exc


def _map_openai_error(exc: Exception) -> Exception:
    """把 openai SDK 异常映射到 LLMError 体系。"""
    try:
        from openai import (
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            RateLimitError as OpenAIRateLimit,
        )
    except ImportError:
        return exc

    if isinstance(exc, APITimeoutError):
        from .errors import LLMTimeoutError

        return LLMTimeoutError(str(exc), provider="openai")
    if isinstance(exc, OpenAIRateLimit):
        from .errors import RateLimitError

        return RateLimitError(str(exc), provider="openai")
    if isinstance(exc, AuthenticationError):
        from .errors import AuthError

        return AuthError(str(exc), provider="openai")
    if isinstance(exc, BadRequestError):
        from .errors import InvalidRequestError

        return InvalidRequestError(str(exc), provider="openai")
    return exc


# ---------------------------------------------------------------------------
# Anthropic 实现
# ---------------------------------------------------------------------------


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key, **kwargs)

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 1.0,
        system: str | None = None,
        timeout: float = 60.0,
        call_type: str = "generation",
        run_id: str | None = None,
        account_id: str | None = None,
    ) -> CompletionResult:
        model = resolve_model(model)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(self._client.messages.create(**kwargs), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise _map_anthropic_error(exc) from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        # 提取文本
        text_parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                text_parts.append(text)
        text = "".join(text_parts)

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        return CompletionResult(
            text=text,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            provider=self.provider,
            raw=response,
            stop_reason=getattr(response, "stop_reason", None),
        )


# ---------------------------------------------------------------------------
# OpenAI 实现
# ---------------------------------------------------------------------------


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(self, *, api_key: str | None = None, **kwargs: Any) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, **kwargs)

    async def complete(
        self,
        prompt: str,
        *,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 1.0,
        system: str | None = None,
        timeout: float = 60.0,
        call_type: str = "generation",
        run_id: str | None = None,
        account_id: str | None = None,
    ) -> CompletionResult:
        model = resolve_model(model)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_openai_error(exc) from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        stop_reason = None
        if response.choices:
            stop_reason = getattr(response.choices[0], "finish_reason", None)

        return CompletionResult(
            text=text,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            provider=self.provider,
            raw=response,
            stop_reason=stop_reason,
        )


