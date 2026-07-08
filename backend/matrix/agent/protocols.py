"""Agent 依赖外部子系统的 Protocol 抽象。

仅定义接口，不做实现。集成层（matrix.kb / matrix.device）后续按这些协议实现。
Agent 节点仅依赖 Protocol，可在 SDK 未就绪时使用 fake/placeholder 实现。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
# 业务通用类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceSlot:
    """调度选出的（设备,账号）槽位。"""

    device_id: UUID
    account_id: UUID
    reason: str = ""


# 抽象一个 callable 工厂方法，便于 mocking：
Notifier = Callable[[str, dict[str, Any]], Awaitable[None]]
