"""令牌桶限速器（按 SDD §3.4.3 实现）。

纯算法模块，不依赖 matrix 业务模块，便于单测。
"""
from __future__ import annotations

import asyncio
from time import monotonic


class RateLimitTimeout(Exception):
    """等待令牌超时。"""


class TokenBucket:
    """异步令牌桶。

    - 桶满 ``capacity`` 个令牌，按 ``refill_rate`` (个/秒) 匀速补充。
    - ``acquire`` 阻塞直到拿到 1 个令牌或超时。
    """

    def __init__(self, capacity: int = 30, refill_rate: float = 1 / 30) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = float(capacity)
        self.last_refill = monotonic()

    def _refill(self) -> None:
        now = monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_refill = now

    async def acquire(self, timeout: float = 600) -> None:
        """阻塞直到拿到 1 个令牌。

        :param timeout: 最长等待秒数，<= 0 立即抛 :class:`RateLimitTimeout`。
        :raises RateLimitTimeout: 等待超时仍无令牌。
        """
        if timeout <= 0:
            self._refill()
            if self.tokens < 1:
                raise RateLimitTimeout("rate limit timeout")
            self.tokens -= 1
            return

        deadline = monotonic() + timeout
        while True:
            self._refill()
            if self.tokens >= 1:
                self.tokens -= 1
                return

            remaining = deadline - monotonic()
            if remaining <= 0:
                raise RateLimitTimeout("rate limit timeout")

            # 等待一个令牌所需的最小时间，但不超过剩余超时。
            wait = min(1 / self.refill_rate, remaining)
            await asyncio.sleep(wait)

    def available(self) -> float:
        """当前可用令牌数（同步读，不触发补充）。"""
        return self.tokens
