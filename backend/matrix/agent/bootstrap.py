"""Agent 服务装配：把具体依赖（LLM / KB / 设备适配器 / 仓库）装进 AgentServices，并产出可执行的 RunManager。

生产路径把真实 ``LLMClient`` / ``Retriever`` / ``ApkHttpClient`` / ``DefaultAgentRepository``
传进来即可，节点代码与状态机无需改动。

v0.6.1：``device_adapter`` 改为必传。生产代码不再有 mock 兜底——忘记传会
立刻 ``RuntimeError``，避免悄悄走到假实现上。
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
from typing import Any

from matrix.agent._services import AgentServices
from matrix.agent.protocols import DeviceInteractor
from matrix.agent.repository import AgentRepository
from matrix.agent.run_manager import RunManager
from matrix.agent.state_machine import build_state_machine

logger = get_logger(__name__)


async def _noop_notifier(name: str, payload: dict[str, Any]) -> None:
    return None


def build_agent_services(
    *,
    llm: Any,
    kb_retriever: Any,
    kb_writer: Any,
    device_adapter: Any,
    notifier: Any | None = None,
    scheduler: Any | None = None,
    task_writer: Any | None = None,
    checkpoint_writer: Any | None = None,
    interaction_writer: Any | None = None,
    rate_limiter: Any | None = None,
    config: Any | None = None,
    model: str = "sonnet",
) -> AgentServices:
    """组装 AgentServices。``device_adapter`` 必传（生产路径 = ``ApkHttpClient``）。

    v0.6：若 ``device_adapter`` 实现了 ``DeviceInteractor`` Protocol，自动作为
    ``device_interactor`` 注入。``interaction_writer`` / ``rate_limiter`` 可选注入。

    测试场景下用 ``tests._fake_adapters.MockDeviceAdapter`` 注入。
    """
    if device_adapter is None:
        raise RuntimeError(
            "device_adapter is required; production path must inject ApkHttpClient, "
            "tests must inject tests._fake_adapters.MockDeviceAdapter"
        )
    if notifier is None:
        notifier = _noop_notifier
    # v0.6: 自动探测 interactor（同 adapter 实现了 DeviceInteractor 就复用）
    device_interactor: DeviceInteractor | None = None
    if isinstance(device_adapter, DeviceInteractor):
        device_interactor = device_adapter
    return AgentServices(
        llm=llm,
        kb_retriever=kb_retriever,
        kb_writer=kb_writer,
        device_publisher=device_adapter,
        device_collector=device_adapter,
        device_interactor=device_interactor,
        notifier=notifier,
        config=config,
        model=model,
        scheduler=scheduler,
        task_writer=task_writer,
        checkpoint_writer=checkpoint_writer,
        interaction_writer=interaction_writer,
        rate_limiter=rate_limiter,
    )


def build_run_manager(
    *,
    services: AgentServices,
    repository: AgentRepository,
    state_machine: Any | None = None,
) -> RunManager:
    """产出可直接 ``create_run`` / ``start_run`` 的 RunManager。

    ``services`` 经此构造后即被注入全局（RunManager 内部调 ``set_services``），
    节点里的 ``get_services()`` 即可取到。
    """
    return RunManager(
        services=services,
        repository=repository,
        state_machine=state_machine or build_state_machine(),
    )


__all__ = ["build_agent_services", "build_run_manager"]
