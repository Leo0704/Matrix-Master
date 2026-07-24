"""Agent 依赖外部子系统的 Protocol 抽象。

仅定义接口，不做实现。集成层（matrix.kb / matrix.device）后续按这些协议实现。
Agent 节点仅依赖 Protocol，可在 SDK 未就绪时使用 fake/placeholder 实现。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

# ---------------------------------------------------------------------------
# 知识库（matrix.kb 集成层落地点）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievedChunk:
    """KB 检索返回的 chunk。"""

    chunk_id: UUID
    doc_id: UUID
    doc_type: str  # 'brand' | 'persona' | 'rule' | 'topic' | 'history' | 'template'
    text: str
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class RetrieveQuery:
    """KB 检索查询。"""

    query: str
    doc_types: tuple[str, ...] = ()
    top_k: int = 5
    filters: dict[str, Any] | None = None
    # 业务隔离（可选）：传入后只命中 business_id == X 或 NULL（全局共享）的文档
    business_id: str | None = None


@runtime_checkable
class KBRetriever(Protocol):
    """Knowledge-base retrieval interface.

    集成层（matrix.kb）实现：
        async def retrieve(self, query): ...
    """

    async def retrieve(self, query: RetrieveQuery) -> list[RetrievedChunk]: ...


@runtime_checkable
class KBWriter(Protocol):
    """KB 写接口（ANALYZE 节点使用，写 history/topic 等）。"""

    async def upsert_document(
        self,
        *,
        doc_type: str,
        ref_id: UUID | None,
        title: str | None,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> UUID:
        """创建或更新一个 kb_documents。"""


@runtime_checkable
class ConfigReader(Protocol):
    """运行时配置读取（app_config 表）。

    集成层（api/_agent_factory.py）实现：每次 ``get`` 调用时从 session 读最新值，
    节点代码与 kb 接口一致走 Protocol 抽象，便于测试替换。
    """

    async def get(self, key: str, default: Any) -> Any: ...


# ---------------------------------------------------------------------------
# 设备（matrix.device 集成层落地点）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublishResult:
    """APK 发布回报。"""

    ok: bool
    note_id: UUID  # matrix.notes.id
    platform_note_id: str | None = None
    platform_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@runtime_checkable
class DevicePublisher(Protocol):
    """设备发布接口。

    集成层（matrix.device）实现：调度器会把它当作 TaskExecutor 调用。
    Agent publish 节点走阻塞式协议（等 APK 回报）。
    """

    async def publish(
        self,
        *,
        device_id: UUID,
        account_id: UUID,
        title: str,
        content: str,
        images: list[str],
        tags: list[str],
        request_id: str,
        timeout: float = 120.0,
    ) -> PublishResult: ...


@runtime_checkable
class DeviceCollector(Protocol):
    """设备回采接口。"""

    async def collect(
        self,
        *,
        device_id: UUID,
        account_id: UUID,
        platform_note_id: str,
        scope: str = "recent_24h",
    ) -> dict[str, int]:
        """返回 {'views','likes','collects','comments','follows_gained'}。"""


# ---------------------------------------------------------------------------
# 互动（v0.6）—— like / comment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InteractResult:
    """APK 互动回报。"""

    ok: bool
    interaction_id: UUID
    error_code: str | None = None
    error_message: str | None = None


@runtime_checkable
class DeviceInteractor(Protocol):
    """设备互动接口（v0.6 MVP：仅 like + comment）。

    集成层（matrix.device）实现：Agent interact 节点走阻塞式协议（等 APK 回报）。
    action 取值：'like' | 'comment'。
    """

    async def interact(
        self,
        *,
        device_id: UUID,
        account_id: UUID,
        action: str,  # 'like' | 'comment'
        target_note_id: str,
        content: str | None = None,
        request_id: str,
        timeout: float = 60.0,
    ) -> InteractResult: ...


# ---------------------------------------------------------------------------
# 业务通用类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceSlot:
    """调度选出的（设备,账号）槽位。"""

    device_id: UUID
    account_id: UUID
    reason: str = ""
    # 写作风格提示（v0.7+：goal/round 扇出时按设备下标轮换；单 run 随机路径为 None）
    style_hint: str | None = None


@dataclass(frozen=True)
class ChosenSlot(DeviceSlot):
    """SlotPicker 选出的最终调度结果，携带计划下发时间。"""

    scheduled_at: datetime | None = None


# 抽象一个 callable 工厂方法，便于 mocking：
Notifier = Callable[[str, dict[str, Any]], Awaitable[None]]
