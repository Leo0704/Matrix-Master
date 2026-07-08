"""抖动注入（按 SDD §3.4.4 实现）。

对数正态分布抖动：``base * exp(N(0, sigma))``。
"""
from __future__ import annotations

import math
from random import normalvariate


def jitter_delay(base: float, sigma: float = 0.5) -> float:
    """返回 ``base`` 乘以 ``exp(normalvariate(0, sigma))``。

    期望值 ≈ ``base * exp(sigma^2 / 2)``，可保持 ``base`` 量级。
    """
    if base < 0:
        raise ValueError("base must be non-negative")
    if sigma < 0:
        raise ValueError("sigma must be non-negative")
    return base * math.exp(normalvariate(0, sigma))
