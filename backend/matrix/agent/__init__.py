"""LangGraph 状态机编排的自主运营 Agent.

Public API:
    - :class:`StateMachine` — LangGraph wrapper
    - :class:`RunManager` — run 生命周期
    - :class:`AgentServices` — 依赖容器
    - :class:`GuardConfig` — guard 阈值
    - :data:`prompts` — LLM prompt 模板

模块布局::

    matrix.agent/
    ├── state_machine.py       # StateGraph + 9 状态 + 转移边
    ├── nodes/                 # 每个状态一个文件
    ├── guards.py              # can_* 转移条件
    ├── checkpoint.py          # agent_checkpoints 读写
    ├── run_manager.py         # RunManager API
    ├── prompts.py             # LLM prompt 模板
    ├── protocols.py           # KB / Device Protocol 抽象
    ├── types.py               # AgentState, State enum
    └── _services.py           # 进程级 DI 单例
"""

from __future__ import annotations

from . import prompts
from ._services import AgentServices, get_services, reset_services, set_services
from .guards import GuardConfig
from .protocols import (
    DeviceCollector,
    DevicePublisher,
    KBRetriever,
    KBWriter,
    RetrievedChunk,
    RetrieveQuery,
)
from .run_manager import (
    RunManager,
    cancel_run,
    create_run,
    get_manager,
    get_run_status,
    init_manager,
    resume_run,
    start_run,
)
from .state_machine import StateMachine, build_state_machine
from .types import (
    AgentState,
    State,
)

__all__ = [
    # 状态机
    "StateMachine",
    "build_state_machine",
    # run 生命周期
    "RunManager",
    "init_manager",
    "get_manager",
    "create_run",
    "start_run",
    "cancel_run",
    "get_run_status",
    "resume_run",
    # 服务
    "AgentServices",
    "set_services",
    "get_services",
    "reset_services",
    # 协议
    "KBRetriever",
    "KBWriter",
    "DevicePublisher",
    "DeviceCollector",
    "RetrieveQuery",
    "RetrievedChunk",
    # 类型 / 配置 / 模板
    "State",
    "AgentState",
    "GuardConfig",
    "prompts",
]
