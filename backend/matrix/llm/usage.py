"""用量统计抽象层。

实际写库由集成层实现；本模块只定义接口与内存聚合。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class UsageRecord:
    """单次 LLM 调用的用量记录。"""

    model: str
    call_type: str  # 'generation' | 'decision' | 'embedding' | ...
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    run_id: str | None = None
    account_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    record_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "model": self.model,
            "call_type": self.call_type,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "run_id": self.run_id,
            "account_id": self.account_id,
            "timestamp": self.timestamp.isoformat(),
        }


class UsageTracker(ABC):
    """用量跟踪器接口。集成层继承并实现持久化。"""

    @abstractmethod
    def record(self, usage: UsageRecord) -> None:
        """记录一次调用。集成层在此写入 llm_usage 表。"""

    @abstractmethod
    def summary(self, *, since: datetime | None = None) -> dict[str, Any]:
        """按 model / call_type 汇总用量与成本。"""


class InMemoryUsageTracker(UsageTracker):
    """进程内聚合（用于测试与单进程场景）。"""

    def __init__(self) -> None:
        self._records: list[UsageRecord] = []

    def record(self, usage: UsageRecord) -> None:
        self._records.append(usage)

    def summary(self, *, since: datetime | None = None) -> dict[str, Any]:
        records = self._records
        if since is not None:
            records = [r for r in records if r.timestamp >= since]

        by_model: dict[str, dict[str, float]] = {}
        by_call_type: dict[str, dict[str, float]] = {}
        total_cost = 0.0
        total_prompt = 0
        total_completion = 0

        for r in records:
            m = by_model.setdefault(
                r.model, {"cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
            )
            m["cost_usd"] += r.cost_usd
            m["prompt_tokens"] += r.prompt_tokens
            m["completion_tokens"] += r.completion_tokens
            m["calls"] += 1

            c = by_call_type.setdefault(
                r.call_type, {"cost_usd": 0.0, "calls": 0}
            )
            c["cost_usd"] += r.cost_usd
            c["calls"] += 1

            total_cost += r.cost_usd
            total_prompt += r.prompt_tokens
            total_completion += r.completion_tokens

        return {
            "total_cost_usd": total_cost,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_calls": len(records),
            "by_model": by_model,
            "by_call_type": by_call_type,
        }

    def records(self) -> list[UsageRecord]:
        return list(self._records)
