"""运行时 Agent 服务装配工厂。

为什么独立一个模块：避免在 ``app.py`` lifespan 内部嵌套大段 class 定义（缩进易乱）。
Retriever / KbStore 需要 session，但 ``AgentServices`` 是进程级单例 — 用懒构造
工厂模式：每次 ``retrieve`` / ``upsert_document`` 调用时从 factory 拿新 session。
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.agent.bootstrap import build_agent_services
from matrix.agent._services import AgentServices
from matrix.kb.embedding import EmbeddingService
from matrix.kb.retrieval import Retriever
from matrix.kb.store import KbStore
from matrix.llm.db_tracker import DbUsageTracker


class _LazyRetriever:
    """懒构造 Retriever — 每次 retrieve 时从 factory 拿新 session。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        embedder: EmbeddingService,
    ) -> None:
        self._factory = factory
        self._embedder = embedder

    async def retrieve(self, query: Any, **kwargs: Any) -> Any:
        async with self._factory() as session:
            r = Retriever(session, self._embedder)
            return await r.retrieve(query, **kwargs)


class _LazyWriter:
    """懒构造 KbStore — 每次写时从 factory 拿新 session。"""

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        embedder: EmbeddingService,
    ) -> None:
        self._factory = factory
        self._embedder = embedder

    async def upsert_document(self, **kwargs: Any) -> Any:
        async with self._factory() as session:
            store = KbStore(session, self._embedder)
            return await store.create_document(**kwargs)


class _LazyConfigReader:
    """懒读 app_config — 每次 get 时从 factory 拿新 session 查最新值。"""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def get(self, key: str, default: Any) -> Any:
        from sqlalchemy import select

        from matrix.db.models import AppConfig

        async with self._factory() as session:
            row = (
                await session.execute(select(AppConfig).where(AppConfig.key == key))
            ).scalar_one_or_none()
            if row is None or row.value is None:
                return default
            # 约定：settings.tsx 写值时包成 ``{"value": <scalar>}``，此处解包
            if isinstance(row.value, dict) and "value" in row.value:
                return row.value["value"]
            return row.value


async def build_runtime_services(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    llm_factory: Callable[[], Any] = lambda: None,
    embedding_client_cls: Callable[..., Any] | None = None,
    task_writer: Any | None = None,
    scheduler: Any | None = None,
) -> AgentServices:
    """构造生产链所需的 AgentServices：LLM / KB 检索 / KB 写库 / Usage 跟踪。

    Args:
        session_factory: DB session 工厂
        llm_factory: 返回 LLMClient 实例的可调用（默认从环境变量选 provider）
        embedding_client_cls: OpenAI Embedding 客户端类（用于构造 EmbeddingService）
        task_writer: 写 task 的 callable（默认 None → dispatch_node 静默跳过落库；
            生产路径传 :class:`matrix.scheduler.db.DbTaskWriter`）
        scheduler: 可选 slot picker；未传时构造默认 :class:`DefaultSlotPicker`
    """
    llm = llm_factory()
    if embedding_client_cls is None:
        # 兜底：尝试 OpenAI Embedding 客户端
        from matrix.llm.embeddings import EmbeddingClient

        embedding_client_cls = EmbeddingClient
    embedder = EmbeddingService(embedding_client_cls())
    usage_tracker = DbUsageTracker(session_factory)

    if scheduler is None:
        from matrix.scheduler import DefaultSlotPicker

        scheduler = DefaultSlotPicker(session_factory)

    services = build_agent_services(
        llm=llm,
        kb_retriever=_LazyRetriever(session_factory, embedder),
        kb_writer=_LazyWriter(session_factory, embedder),
        usage_tracker=usage_tracker,
        config=_LazyConfigReader(session_factory),
        task_writer=task_writer,
        scheduler=scheduler,
    )
    return services
