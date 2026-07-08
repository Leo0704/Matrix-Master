"""Agent run 数据访问层 Protocol。

把 RunManager 对 ORM 的依赖抽象为 Repository Protocol；生产实现是
``DefaultAgentRepository``（用 SQLAlchemy async session 写 agent_runs /
agent_checkpoints 表），测试可注入 ``InMemoryAgentRepository`` 或 AsyncMock。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class AgentCheckpointRow(Protocol):
    run_id: UUID
    ts: datetime
    from_state: str
    to_state: str
    payload: dict[str, Any] | None


@runtime_checkable
class AgentRunRow(Protocol):
    id: UUID
    goal_id: UUID | None
    current_state: str
    payload: dict[str, Any] | None
    status: str
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None


class AgentRepository(Protocol):
    """Agent 状态机对持久层的最小依赖抽象。"""

    async def create_run(
        self,
        *,
        run_id: UUID,
        goal_id: UUID | None,
        payload: dict[str, Any],
        started_at: datetime,
        current_state: str,
        status: str,
    ) -> None: ...

    async def write_checkpoint(
        self,
        *,
        run_id: UUID,
        from_state: str,
        to_state: str,
        payload: dict[str, Any] | None,
        ts: datetime | None = None,
    ) -> None: ...

    async def read_last_checkpoint(
        self, run_id: UUID
    ) -> AgentCheckpointRow | None: ...

    async def read_all_checkpoints(
        self, run_id: UUID
    ) -> list[AgentCheckpointRow]: ...

    async def get_run(self, run_id: UUID) -> AgentRunRow | None: ...

    async def update_run(
        self,
        run_id: UUID,
        *,
        current_state: str | None = None,
        status: str | None = None,
        payload_merge: dict[str, Any] | None = None,
        ended_at: datetime | None = None,
    ) -> None: ...


__all__ = [
    "AgentRepository",
    "AgentRunRow",
    "AgentCheckpointRow",
]
