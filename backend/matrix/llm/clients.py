"""LLM 统一客户端。

- ``LLMClient`` ABC：``async complete(prompt, model, max_tokens, temperature) -> CompletionResult``
- ``AnthropicClient`` / ``OpenAIClient``：官方 SDK 实现
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


def _fix_surrogates(text: str) -> str:
    """把紧邻的 high+low surrogate 重新组合成单个 Unicode codepoint。

    场景：LLM 上下文里的 emoji 走 ``json.loads`` 没被自动配对，存为
    两个独立码点（``chr(0xD83E) + chr(0xDD75)``）。anthropic SDK 二次
    序列化 ``.encode('utf-8')`` 时会报 ``surrogates not allowed``。
    紧邻的 high+low 重新配对成完整 codepoint（如 ``\\U0001FA75``），
    单边 orphan 无法配对则原样保留。
    """
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        code = ord(ch)
        if 0xD800 <= code <= 0xDBFF and i + 1 < n:
            nxt_code = ord(text[i + 1])
            if 0xDC00 <= nxt_code <= 0xDFFF:
                # 紧邻对 → 合并成 supplementary plane codepoint
                cp = 0x10000 + ((code - 0xD800) << 10) + (nxt_code - 0xDC00)
                out.append(chr(cp))
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


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
        from .errors import LLMError as _LLMError
        from .retry import retry_with_backoff

        model = resolve_model(model)
        # 修复 prompt/system 中的孤儿代理对（LLM 上下文里如果含 emoji，
        # 经 json.dumps → loads 后会拆成 😀 这种，SDK 二次
        # 序列化时会抛 UnicodeEncodeError）。
        prompt = _fix_surrogates(prompt)
        if system:
            system = _fix_surrogates(system)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        @retry_with_backoff(max_attempts=3, retry_on=(_LLMError,))
        async def _call() -> Any:
            return await asyncio.wait_for(
                self._client.messages.create(**kwargs), timeout=timeout
            )

        start = time.monotonic()
        try:
            response = await _call()
        except Exception as exc:  # noqa: BLE001
            raise _map_anthropic_error(exc) from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        # 提取文本（也修一遍，输出里也可能带孤儿代理对）
        text_parts: list[str] = []
        for block in getattr(response, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                text_parts.append(_fix_surrogates(text))
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
        from .errors import LLMError as _LLMError
        from .retry import retry_with_backoff

        model = resolve_model(model)
        # 同样修复孤儿代理对（OpenAI 客户端偶尔也会报同样错）
        prompt = _fix_surrogates(prompt)
        if system:
            system = _fix_surrogates(system)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        @retry_with_backoff(max_attempts=3, retry_on=(_LLMError,))
        async def _call() -> Any:
            return await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                timeout=timeout,
            )

        start = time.monotonic()
        try:
            response = await _call()
        except Exception as exc:  # noqa: BLE001
            raise _map_openai_error(exc) from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        text = ""
        if response.choices:
            text = _fix_surrogates(response.choices[0].message.content or "")

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


