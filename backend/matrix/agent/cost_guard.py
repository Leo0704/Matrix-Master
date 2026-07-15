"""LLM 成本护栏（Phase 2a B）。

老板怕的是：某天某 goal 写嗨了，DRAFT/REVIEW/REVISE 翻倍调 LLM，月底账单一
看几十万 token。两条硬护栏：

1. 单 goal 单日 token 上限（默认 200k；防一个 goal 把配额烧光）
2. 全局单日 token 上限（默认 5M；防一整批 goal 同时发疯）

计数源：
- 优先用 LLM 返回的 ``prompt_tokens + completion_tokens``（精确）
- 拿不到时用 ``max_tokens + len(prompt)//4`` 估（不精确但有上限）

原子计数走 :class:`matrix.scheduler.rate_limiter.DbDailyCounter` —— 多 uvicorn
worker 共用同一张 ``daily_counters`` 表，INSERT ... ON CONFLICT DO UPDATE
把 race condition 关掉。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)


class LLMCostLimitExceeded(RuntimeError):
    """LLM 成本超限：抛给 ``llm_complete`` 的 caller 决定怎么办（收工/降级/通知）。"""

    def __init__(self, scope: str, used: int, limit: int, key: str = "") -> None:
        self.scope = scope  # 'goal' | 'global'
        self.used = used
        self.limit = limit
        self.key = key
        super().__init__(
            f"LLM cost limit exceeded: {scope}={key or 'GLOBAL'} "
            f"used={used}/{limit} tokens (today)"
        )


@dataclass
class CostGuard:
    """LLM 成本护栏：每次 LLM 调用完计数 + 检双上限。

    计数器 key：
    - per-goal: ``scope='llm_cost', key=<goal_id>, kind='tokens'``
    - global:   ``scope='llm_cost', key='GLOBAL',      kind='tokens'``

    阈值常量定义在类内（class-level），方便改；生产可读 env 覆盖。
    """

    counter: Any  # 期望 :class:`DbDailyCounter`，用 duck type 不绑死 import
    per_goal_limit: int = 200_000
    global_limit: int = 5_000_000
    enabled: bool = True

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """拿不到 usage 时的兜底：每 4 字符 ≈ 1 token（粗估，cl100k 系约 3.5）。"""
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _today(self) -> date:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).date()

    async def record(
        self,
        *,
        goal_id: str | None,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """记一笔（用 LLM 真值或估的值），并检查双上限。"""
        if not self.enabled or self.counter is None:
            return
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return
        tokens = int(prompt_tokens) + int(completion_tokens)
        day = self._today()
        try:
            # 1) per-goal 计数 + 检查
            if goal_id:
                gid = str(goal_id)
                used_goal = await self.counter.add(
                    "llm_cost", gid, "tokens", day, amount=tokens
                )
                if used_goal > self.per_goal_limit:
                    raise LLMCostLimitExceeded(
                        "goal", used_goal, self.per_goal_limit, gid
                    )
            # 2) global 计数 + 检查
            used_global = await self.counter.add(
                "llm_cost", "GLOBAL", "tokens", day, amount=tokens
            )
            if used_global > self.global_limit:
                raise LLMCostLimitExceeded(
                    "global", used_global, self.global_limit
                )
            logger.debug(
                "llm.cost.recorded",
                tokens=tokens,
                goal_id=goal_id,
                used_goal=used_goal if goal_id else None,
                used_global=used_global,
            )
        except LLMCostLimitExceeded:
            raise
        except Exception:
            # 计数器挂掉绝不应挡主流程；记 warning 就完事
            logger.warning("llm.cost.counter_failed", exc_info=True)


__all__ = ["CostGuard", "LLMCostLimitExceeded"]
