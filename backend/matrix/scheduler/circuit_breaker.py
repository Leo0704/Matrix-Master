"""熔断器（按 SDD §3.4.5 实现）。

纯算法模块，不依赖 matrix 业务模块。

**重要：进程内实例**——``failures`` 列表是 list[float]，所有计数都存在
当前 Python 进程的内存里。uvicorn 跑 N 个 worker 时，每个 worker 各持
一份独立 breaker，一个 worker 熔断了不影响其他 worker。

适用场景：单进程 / 单 worker（Docker 默认 CMD 不带 ``--workers``）OK。
多 worker 想共享计数需要切到 Redis / DB 计数器（后续 Phase）。
"""
from __future__ import annotations

from time import monotonic


class CircuitOpen(Exception):
    """熔断打开时由执行器抛出，携带冷却剩余秒数。

    调度器捕获后应把任务退回 pending 并把 scheduled_at 推迟到冷却结束，
    而不是 mark_failed（熔断是设备级故障，不是任务本身的错）。
    """

    def __init__(self, retry_after: float) -> None:
        super().__init__(f"circuit open, retry after {retry_after:.0f}s")
        self.retry_after = max(0.0, retry_after)


class CircuitBreaker:
    """滑动窗口失败计数熔断器。

    - ``window`` 秒内的失败累计达到 ``threshold``，熔断打开 ``cool_off`` 秒。
    - ``is_open()`` 在冷却期内返回 True，调用方应放弃 / 排队。

    注意：实例是**进程内**的；多 worker / 多节点部署需要外部共享存储。
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

    def record_failure(self) -> bool:
        """记一次失败；返回 True 表示本次触发了 closed→open 边界。

        调用方（如执行器）用这个返回值实现"只在熔断刚打开时告警一次"，
        避免熔断期间每条任务都刷一条告警。
        """
        now = monotonic()
        was_open = now < self.open_until
        self.failures.append(now)
        self._prune(now)
        if len(self.failures) >= self.threshold:
            self.open_until = now + self.cool_off
        return (not was_open) and monotonic() < self.open_until

    def is_open(self) -> bool:
        return monotonic() < self.open_until

    def retry_after(self) -> float:
        """距熔断关闭的剩余秒数；已关闭返回 0。"""
        return max(0.0, self.open_until - monotonic())

    def reset(self) -> None:
        """手动重置（主要用于测试）。"""
        self.failures.clear()
        self.open_until = 0.0


class PerDeviceCircuitBreaker:
    """按 device_id 分桶的熔断器注册表。

    每台设备独立的失败计数与冷却，A 设备熔断不连坐 B 设备。
    构造参数与 :class:`CircuitBreaker` 相同，作为每个分桶的模板。
    仍是**进程内**实例（多 worker 不共享，同 CircuitBreaker 的限制）。
    """

    def __init__(self, window: int = 600, threshold: int = 5, cool_off: int = 1800) -> None:
        self.window = window
        self.threshold = threshold
        self.cool_off = cool_off
        self._buckets: dict[str, CircuitBreaker] = {}

    def _bucket(self, device_id: object) -> CircuitBreaker:
        key = str(device_id) if device_id is not None else ""
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = CircuitBreaker(self.window, self.threshold, self.cool_off)
            self._buckets[key] = bucket
        return bucket

    def is_open(self, device_id: object) -> bool:
        return self._bucket(device_id).is_open()

    def record_failure(self, device_id: object) -> bool:
        """给该设备记一次失败；返回 True 表示该设备本次 closed→open。"""
        return self._bucket(device_id).record_failure()

    def retry_after(self, device_id: object) -> float:
        return self._bucket(device_id).retry_after()

    def reset(self, device_id: object | None = None) -> None:
        """重置单台设备；device_id=None 时重置全部（主要用于测试）。"""
        if device_id is None:
            self._buckets.clear()
        else:
            self._bucket(device_id).reset()
