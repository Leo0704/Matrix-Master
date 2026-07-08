"""Agent 服务装配：把具体依赖（LLM / KB / 设备适配器 / 仓库）装进 AgentServices，并产出可执行的 RunManager。

开发 / 测试用 :func:`build_agent_services` + :func:`build_run_manager` 即可在没有
真实 LLM / APK / 数据库的情况下跑通整条闭环（设备适配器缺省用 MockDeviceAdapter）。
生产路径把真实 ``LLMClient`` / ``Retriever`` / ``ApkHttpClient`` / ``DefaultAgentRepository``
传进来即可，节点代码与状态机无需改动。
"""
from __future__ import annotations

import logging
from typing import Any

from matrix.agent._services import AgentServices
from matrix.agent.repository import AgentRepository
from matrix.agent.run_manager import RunManager
from matrix.agent.state_machine import build_state_machine
from matrix.device.adapters import MockDeviceAdapter

logger = logging.getLogger(__name__)


async def _noop_notifier(name: str, payload: dict[str, Any]) -> None:
    return None


def build_agent_services(
    *,
    llm: Any,
    kb_retriever: Any,
    kb_writer: Any,
    device_adapter: Any | None = None,
    notifier: Any | None = None,
    scheduler: Any | None = None,
    task_writer: Any | None = None,
    checkpoint_writer: Any | None = None,
    model: str = "sonnet",
) -> AgentServices:
    """组装 AgentServices。device_adapter 缺省用 MockDeviceAdapter（无手机也能跑）。"""
    if device_adapter is None:
        device_adapter = MockDeviceAdapter()
    if notifier is None:
        notifier = _noop_notifier
    return AgentServices(
        llm=llm,
        kb_retriever=kb_retriever,
        kb_writer=kb_writer,
        device_publisher=device_adapter,
        device_collector=device_adapter,
        notifier=notifier,
        model=model,
        scheduler=scheduler,
        task_writer=task_writer,
        checkpoint_writer=checkpoint_writer,
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
