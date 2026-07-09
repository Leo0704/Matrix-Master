"""重试装饰器：指数退避 1s/3s/9s，最多 3 次；尊重 retry-after 头。"""

from __future__ import annotations

import asyncio
import functools
from matrix.monitoring.logging import get_logger
import random
from typing import Any, Awaitable, Callable, TypeVar

from .errors import LLMError, RateLimitError

logger = get_logger(__name__)

T = TypeVar("T")

# 默认退避序列（秒）
DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 3.0, 9.0)


def _parse_retry_after(exc: BaseException) -> float | None:
    """从异常对象中提取 retry-after。SDK 异常通常挂在 exc.response.headers / exc.headers。"""
    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers:
            value = headers.get("retry-after") or headers.get("Retry-After")
            if value is not None:
                try:
                    return max(0.0, float(value))
                except (TypeError, ValueError):
                    return None
    headers = getattr(exc, "headers", None)
    if headers:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value is not None:
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return None
    return None


def retry_with_backoff(
    *,
    max_attempts: int = 3,
    backoff: tuple[float, ...] = DEFAULT_BACKOFF,
    retry_on: tuple[type[BaseException], ...] = (LLMError,),
    respect_retry_after: bool = True,
    jitter: float = 0.1,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """异步函数重试装饰器。

    - 最多尝试 ``max_attempts`` 次（首次失败后最多重试 max_attempts-1 次）。
    - 退避序列 ``backoff``，遇 RateLimitError 且响应带 retry-after 则取其值。
    - ``retry_on`` 内异常可重试；其他异常立即抛出。
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exc: BaseException | None = None
            total = max(1, max_attempts)
            for attempt in range(1, total + 1):
                try:
                    return await fn(*args, **kwargs)
                except retry_on as exc:  # type: ignore[misc]
                    last_exc = exc
                    if attempt >= total:
                        break
                    delay = backoff[min(attempt - 1, len(backoff) - 1)]
                    if respect_retry_after and isinstance(exc, RateLimitError):
                        ra = _parse_retry_after(exc)
                        if ra is not None:
                            delay = max(delay, ra)
                    if jitter > 0:
                        delay = delay * (1.0 + random.uniform(-jitter, jitter))
                        delay = max(0.0, delay)
                    logger.warning(
                        "llm.retry",
                        attempt=attempt,
                        total=total,
                        delay=delay,
                        err=exc,
                    )
                    await asyncio.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator
