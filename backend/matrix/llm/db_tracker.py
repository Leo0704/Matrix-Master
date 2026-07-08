"""DbUsageTracker：把 LLM 调用的用量 / 成本写入 ``llm_usage`` 表。

每条记录 = 一次 LLM 调用。供前端 /metrics/summary 聚合 + 成本告警。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.db.models import LlmUsage
from matrix.llm.usage import UsageRecord, UsageTracker

logger = logging.getLogger(__name__)


class DbUsageTracker(UsageTracker):
    """每次 ``record()`` 异步写一行 ``llm_usage``。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = session_factory

    def record(self, usage: UsageRecord) -> None:
        """同步接口（与 UsageTracker ABC 一致）。内部 schedule 异步写库。"""
        # 真正的写库在 record_async；这里保持同步以匹配 UsageTracker 接口
        # 实际节点 / 路由的 LLM 调用已经 await 了 LLMClient.complete，
        # 它们调 ``record_async`` 即可；同步 record 走 logger.warning
        logger.warning(
            "DbUsageTracker.record called synchronously; use record_async instead"
        )

    async def record_async(self, usage: UsageRecord) -> None:
        """异步写一行 ``llm_usage``。LLMClient 调用方应 await 这个。"""
        try:
            async with self._factory() as session:
                await session.execute(
                    insert(LlmUsage).values(
                        ts=usage.timestamp or datetime.utcnow(),
                        model=usage.model,
                        call_type=usage.call_type,
                        prompt_tokens=usage.prompt_tokens,
                        completion_tokens=usage.completion_tokens,
                        total_tokens=usage.prompt_tokens + usage.completion_tokens,
                        cost_usd=usage.cost_usd,
                        latency_ms=usage.latency_ms,
                        run_id=usage.run_id,
                        account_id=usage.account_id,
                    )
                )
                await session.commit()
        except Exception as e:  # pragma: no cover
            logger.warning(
                "DbUsageTracker.record_async failed model=%s err=%s", usage.model, e
            )

    def summary(self, *, since: Optional[datetime] = None) -> dict[str, Any]:
        """同步摘要接口 — DbUsageTracker 走 DB 实时查询，不在内存聚合。"""
        # 留作接口兼容；如需前端实时摘要，调 api 层 endpoint 即可
        return {}


__all__ = ["DbUsageTracker"]
