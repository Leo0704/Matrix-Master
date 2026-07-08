"""Agent 子系统依赖容器（DI）。

state machine 的节点函数需要 LLMClient / KBRetriever / DevicePublisher 等依赖。
为避免污染 AgentState（langgraph 只对它做 partial update，多余字段会被丢），
本模块把依赖放在进程级单例上；测试可通过 ``set_services(...)`` 注入 mock。

生产路径由 ``matrix.agent.run_manager.RunManager`` 在启动时调用
``set_services(real_services)``。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from matrix.llm import LLMClient
from matrix.llm.errors import LLMError

from matrix.llm.clients import calculate_cost_usd
from matrix.llm.usage import UsageTracker

from .protocols import (
    DeviceCollector,
    DevicePublisher,
    KBRetriever,
    KBWriter,
    Notifier,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentServices:
    """Agent 节点依赖集合。"""

    llm: LLMClient
    kb_retriever: KBRetriever
    kb_writer: KBWriter
    device_publisher: DevicePublisher
    device_collector: DeviceCollector
    notifier: Notifier
    # LLM 用量跟踪（DB 持久化；如未设置则 LLM 调用不写 usage）
    usage_tracker: UsageTracker | None = None
    # 默认模型与生成参数
    model: str = "sonnet"
    max_tokens: int = 1024
    temperature: float = 0.7
    # prompt 配置覆盖（运行时 persona / rules 来自 KB，Agent 不固化）
    system_metadata: dict[str, Any] = field(default_factory=dict)
    # 写 checkpoint 默认 callable（测试可换）
    checkpoint_writer: Callable[..., Awaitable[Any]] | None = None
    # 写 task 默认 callable（test 可换；None 则跳过持久化）
    task_writer: Callable[..., Awaitable[Any]] | None = None
    # 调度选（设备/账号）— 由 RunManager 注入；默认 None 调度节点会返回占位 slot
    scheduler: Any | None = None


_SERVICES: AgentServices | None = None


def set_services(services: AgentServices) -> None:
    """设置全局依赖（生产路径 / 测试）。"""
    global _SERVICES
    _SERVICES = services
    logger.debug("agent services set: llm=%s", services.llm)


def get_services() -> AgentServices:
    """获取全局依赖；未初始化则抛错（提示先调 set_services）。"""
    if _SERVICES is None:
        raise RuntimeError(
            "AgentServices not initialized; call matrix.agent.set_services(...) "
            "or RunManager.create_run() in production"
        )
    return _SERVICES


def reset_services() -> None:
    """清空单例（测试用）。"""
    global _SERVICES
    _SERVICES = None


# ---------------------------------------------------------------------------
# LLM 便捷调用 + 内联指数退避
# ---------------------------------------------------------------------------

_DEFAULT_BACKOFF: tuple[float, ...] = (1.0, 3.0, 9.0)


async def llm_complete(
    system: str | None,
    user: str,
    *,
    services: AgentServices | None = None,
    retries: int = 3,
    call_type: str = "generation",
    run_id: str | None = None,
    account_id: str | None = None,
) -> str:
    """调用 LLM，失败按指数退避 1s/3s/9s，最多 retries 次。返回生成文本。"""
    svc = services or get_services()
    last_exc: BaseException | None = None
    total = max(1, retries)
    for attempt in range(1, total + 1):
        try:
            result = await svc.llm.complete(
                user,
                model=svc.model,
                max_tokens=svc.max_tokens,
                temperature=svc.temperature,
                system=system,
                call_type=call_type,
                run_id=run_id,
                account_id=account_id,
            )
            # 写 usage 记录到 DB（如果有 tracker）
            if svc.usage_tracker is not None:
                try:
                    from matrix.llm.usage import UsageRecord

                    cost = calculate_cost_usd(
                        result.model, result.prompt_tokens, result.completion_tokens
                    )
                    rec = UsageRecord(
                        model=result.model,
                        call_type=call_type,
                        prompt_tokens=result.prompt_tokens,
                        completion_tokens=result.completion_tokens,
                        cost_usd=cost,
                        latency_ms=result.latency_ms,
                        run_id=run_id,
                        account_id=account_id,
                    )
                    if hasattr(svc.usage_tracker, "record_async"):
                        await svc.usage_tracker.record_async(rec)
                except Exception:  # pragma: no cover
                    logger.exception("usage_tracker.record_async failed")
            return result.text
        except LLMError as exc:  # 可重试错误
            last_exc = exc
            if attempt >= total:
                break
            delay = _DEFAULT_BACKOFF[min(attempt - 1, len(_DEFAULT_BACKOFF) - 1)]
            delay *= 1.0 + random.uniform(-0.1, 0.1)
            delay = max(0.0, delay)
            logger.warning(
                "agent.llm.retry attempt=%d/%d delay=%.2fs err=%s",
                attempt,
                total,
                delay,
                exc,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


__all__ = [
    "AgentServices",
    "set_services",
    "get_services",
    "reset_services",
    "llm_complete",
]
