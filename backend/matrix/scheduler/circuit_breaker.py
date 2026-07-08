"""熔断器（按 SDD §3.4.5 实现）。

纯算法模块，不依赖 matrix 业务模块。
"""
from __future__ import annotations

from time import monotonic


class CircuitBreaker:
    """滑动窗口失败计数熔断器。

    - ``window`` 秒内的失败累计达到 ``threshold``，熔断打开 ``cool_off`` 秒。
    - ``is_open()`` 在冷却期内返回 True，调用方应放弃 / 排队。
    """

    def __init__(self, window: int = 600, threshold: int = 5, cool_off: int = 1800) -> None:
        if window <= 0:
            raise ValueError("window must be positive")
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        if cool_off < 0:
            raise ValueError("cool_off must be non-negative")
        self.window = window
        self.threshold = threshold
        self.cool_off = cool_off
        self.failures: list[float] = []
        self.open_until: float = 0.0

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        # 失败时间戳按单调递增，bisect 即可；这里用线性切片保持简单。
        kept = [t for t in self.failures if t >= cutoff]
        self.failures = kept

    def record_failure(self) -> None:
        now = monotonic()
        self.failures.append(now)
        self._prune(now)
        if len(self.failures) >= self.threshold:
            self.open_until = now + self.cool_off

    def is_open(self) -> bool:
        return monotonic() < self.open_until

    def reset(self) -> None:
        """手动重置（主要用于测试）。"""
        self.failures.clear()
        self.open_until = 0.0
