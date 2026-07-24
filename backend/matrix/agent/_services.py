"""Agent 子系统依赖容器（DI）。

state machine 的节点函数需要 LLMClient / KBRetriever / DevicePublisher 等依赖。
为避免污染 AgentState（langgraph 只对它做 partial update，多余字段会被丢），
本模块把依赖放在进程级单例上；测试可通过 ``set_services(...)`` 注入 mock。

生产路径由 ``matrix.agent.run_manager.RunManager`` 在启动时调用
``set_services(real_services)``。
"""
from __future__ import annotations

import asyncio
from matrix.monitoring.logging import get_logger
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from matrix.llm import LLMClient
from matrix.llm.errors import LLMError
from matrix.scheduler.token_bucket import RateLimitTimeout

from .protocols import (
    ConfigReader,
    DeviceCollector,
    DeviceInteractor,
    DevicePublisher,
    KBRetriever,
    KBWriter,
    Notifier,
)

logger = get_logger(__name__)


@dataclass
class AgentServices:
    """Agent 节点依赖集合。"""

    # 必需依赖
    llm: LLMClient
    kb_retriever: KBRetriever
    kb_writer: KBWriter
    device_publisher: DevicePublisher
    device_collector: DeviceCollector
    notifier: Notifier
    # v0.6 互动
    device_interactor: DeviceInteractor | None = None
    # 运行时配置（app_config 表读阈值等）；测试可传 None → 节点走硬编码 fallback
    config: ConfigReader | None = None
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
    # 写 notes 草稿 callable（v0.7 Phase 5：DRAFT 节点落库，签名为 async (record: dict) -> UUID；None 则跳过持久化）
    note_writer: Callable[..., Awaitable[Any]] | None = None
    # 写 interaction 记录 callable（v0.6；签名为 async (record: dict) -> UUID；None 则只返 state 不落库）
    interaction_writer: Callable[..., Awaitable[Any]] | None = None
    # 限速器（v0.6 互动节点用；None 则跳过限速，dev 模式）
    rate_limiter: Any | None = None
    # 调度选（设备/账号）— 由 RunManager 注入；默认 None 调度节点会返回占位 slot
    scheduler: Any | None = None
    # v0.7+ round-level allocator：goal/round 扇出时由 orchestrator 调；
    # None 时 _prepare_round 走降级路径（按 N 份占位 brief 生成 run，跳过设备预分配）
    round_allocator: Any | None = None
    # v0.7 Phase 3：生图客户端（ImageGenClient）。None 则 IMAGE_GEN 走 fallback=no_image
    image_generator: Any | None = None
    # v0.7+ 第 2 期：LLM 全局并发 + 每模型限速；None 则跳过限速（dev/test）
    llm_rate_limiter: Any | None = None
    # Phase 2b：DB session 工厂（interact 节点查重 + adaptive 用）。None → 跳过这些特性。
    session_factory: Any | None = None


_SERVICES: AgentServices | None = None


def set_services(services: AgentServices) -> None:
    """设置全局依赖（生产路径 / 测试）。"""
    global _SERVICES
    _SERVICES = services
    logger.debug("agent services set", llm=services.llm)


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
    """调用 LLM，失败按指数退避 1s/3s/9s，最多 retries 次。返回生成文本。

    v0.7+ 第 2 期：可选接入 ``llm_rate_limiter``（全局并发 + 每模型令牌桶）。
    抢不到令牌直接抛 :class:`RateLimitTimeout`，不进入退避重试（限速超时和 LLM 失败语义不同）。
    整个逻辑调用（含重试退避）始终持着同一把 slot，到终态（成功/抛 LLMError/超限速）才释放。
    """
    svc = services or get_services()
    rate = getattr(svc, "llm_rate_limiter", None)
    model = svc.model
    last_exc: BaseException | None = None
    total = max(1, retries)
    if rate is not None:
        await rate.acquire(model)  # RateLimitTimeout 会传出去
    try:
        for attempt in range(1, total + 1):
            try:
                result = await svc.llm.complete(
                    user,
                    model=model,
                    max_tokens=svc.max_tokens,
                    temperature=svc.temperature,
                    system=system,
                    call_type=call_type,
                    run_id=run_id,
                    account_id=account_id,
                )
                return result.text
            except LLMError as exc:  # 可重试错误
                last_exc = exc
                if attempt >= total:
                    break
                delay = _DEFAULT_BACKOFF[min(attempt - 1, len(_DEFAULT_BACKOFF) - 1)]
                delay *= 1.0 + random.uniform(-0.1, 0.1)
                delay = max(0.0, delay)
                logger.warning(
                    "agent.llm.retry",
                    attempt=attempt,
                    total=total,
                    delay=delay,
                    err=exc,
                )
                await asyncio.sleep(delay)
        assert last_exc is not None
        raise last_exc
    finally:
        if rate is not None:
            rate.release(model)


__all__ = [
    "AgentServices",
    "set_services",
    "get_services",
    "reset_services",
    "llm_complete",
]
