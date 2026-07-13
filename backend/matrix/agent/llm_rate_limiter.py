"""LLM 调用的全局并发 + 每模型限速。

解决 agent 在多目标并发 + round-level fan-out 时无任何 LLM 限速、容易撞 Provider 429 的隐患（P1-1）。

两层防护：
  1) 全局并发上限（``asyncio.Semaphore``）：撑住所有模型加起来的瞬时压力。
  2) 每模型令牌桶（复用 ``scheduler.token_bucket.TokenBucket``）：单个模型不被打爆，也不让"高频模型"挤掉别的模型。

设计动机：scheduler.RateLimiter 只管 device 动作（pubish/interact 的账号日上限），
不能直接套在 LLM 上（keying 维度、限速维度都不同——LLM 是按 model+provider 算，
device 是按 account+action 算）。所以另起一个轻量 wrapper。

测试 / 开发用法：让 ``AgentServices.llm_rate_limiter`` 为 ``None`` 即可。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from matrix.monitoring.logging import get_logger
from matrix.scheduler.token_bucket import RateLimitTimeout, TokenBucket

logger = get_logger(__name__)


@dataclass
class LLMRateLimiter:
    """LLM 调用的全局+每模型限速。

    Parameters:
      semaphore: 全局并发上限（持锁数 = 在飞的 LLM 请求数）。
      per_model_capacity: 单模型的令牌桶容量（突发上限）。
      per_model_refill_rate: 单模型的令牌补充速度（个/秒；默认 1.0 = 60 RPM / 模型）。
      timeout: ``acquire`` 等待单模型令牌的最大秒数。
    """

    semaphore: asyncio.Semaphore
    per_model_capacity: int = 5
    per_model_refill_rate: float = 1.0
    timeout: float = 30.0
    _buckets: dict[str, TokenBucket] = field(default_factory=dict)
    _buckets_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def _get_bucket(self, model: str) -> TokenBucket:
        """按 model 懒构造 TokenBucket，线程（协程）安全。"""
        async with self._buckets_lock:
            bucket = self._buckets.get(model)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=self.per_model_capacity,
                    refill_rate=self.per_model_refill_rate,
                )
                self._buckets[model] = bucket
            return bucket

    async def acquire(self, model: str) -> None:
        """抢两把锁：全局 semaphore + 该 model 的令牌桶。

        任一失败都确保释放已抢到的（不让 semaphore 漏掉）；
        超时抛 :class:`RateLimitTimeout`。
        """
        try:
            await asyncio.wait_for(
                self.semaphore.acquire(), timeout=self.timeout
            )
        except (asyncio.TimeoutError, RateLimitTimeout) as exc:
            raise RateLimitTimeout(
                f"llm_rate_limiter semaphore acquire failed for {model}"
            ) from exc

        try:
            bucket = await self._get_bucket(model)
            try:
                await bucket.acquire(timeout=self.timeout)
            except RateLimitTimeout:
                # 给该模型等令牌超时：放掉已经抢到的 semaphore
                self.semaphore.release()
                logger.warning(
                    "llm_rate_limiter.token_timeout",
                    model=model,
                    timeout=self.timeout,
                )
                raise
        except BaseException:
            # 兜底：任何异常都释放 semaphore，避免死锁
            self.semaphore.release()
            raise

    def release(self, model: str) -> None:
        """放回 semaphore。令牌桶不释放——令牌消费即扣，与 TokenBucket 契约一致。"""
        self.semaphore.release()

    def snapshot(self, model: str | None = None) -> dict[str, float]:
        """诊断用：读各模型桶的当前令牌数（或读单个）。"""
        if model is not None:
            b = self._buckets.get(model)
            return {model: b.available() if b else 0.0}
        return {m: b.available() for m, b in self._buckets.items()}


__all__ = ["LLMRateLimiter"]
