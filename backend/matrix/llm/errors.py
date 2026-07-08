"""LLM 异常类型定义。"""

from __future__ import annotations


class LLMError(Exception):
    """LLM 调用基类异常。"""

    def __init__(self, message: str, *, provider: str | None = None, model: str | None = None):
        super().__init__(message)
        self.provider = provider
        self.model = model


class RateLimitError(LLMError):
    """限速错误（429）。尊重 retry-after 头。"""


class LLMTimeoutError(LLMError):
    """调用超时。"""


class AuthError(LLMError):
    """鉴权失败（401/403）。"""


class InvalidRequestError(LLMError):
    """请求参数错误（400）。"""


# 便捷别名，便于 `from matrix.llm.errors import TimeoutError`
TimeoutError = LLMTimeoutError
