"""运行时 Agent 服务装配工厂。

为什么独立一个模块：避免在 ``app.py`` lifespan 内部嵌套大段 class 定义（缩进易乱）。
Retriever / KbStore 需要 session，但 ``AgentServices`` 是进程级单例 — 用懒构造
工厂模式：每次 ``retrieve`` / ``upsert_document`` 调用时从 factory 拿新 session。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matrix.agent.bootstrap import build_agent_services, db_note_writer
from matrix.agent._services import AgentServices
from matrix.agent.llm_rate_limiter import LLMRateLimiter
from matrix.kb.embedding import EmbeddingService
from matrix.kb.retrieval import Retriever
from matrix.kb.store import KbStore


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
            # 兼容 RetrieveQuery dataclass / dict / 关键字
            if hasattr(query, "query"):
                text = query.query
                type_ = kwargs.pop("type", getattr(query, "doc_type", None) or _doc_type_from_doc_types(getattr(query, "doc_types", None)))
                top_k = kwargs.pop("top_k", getattr(query, "top_k", 5))
            elif isinstance(query, dict):
                text = query.get("query", "")
                type_ = kwargs.pop("type", query.get("type") or _doc_type_from_doc_types(query.get("doc_types")))
                top_k = kwargs.pop("top_k", query.get("top_k", 5))
            else:
                text, type_, top_k = query, kwargs.pop("type", ""), kwargs.pop("top_k", 5)
            return await r.retrieve(text, type=type_ or "history", top_k=top_k, **kwargs)


def _doc_type_from_doc_types(doc_types: Any) -> str:
    if not doc_types:
        return ""
    # 单一 doc_types 走最常用值；多值取第一个
    if isinstance(doc_types, (list, tuple, set)):
        return next(iter(doc_types), "")
    return str(doc_types)


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

    # 兼容 alert_scanner / watchdog 直接 await reader(key) 的调用风格
    async def __call__(self, key: str, default: Any = None) -> Any:
        return await self.get(key, default)

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
    notifier: Any | None = None,
) -> AgentServices:
    """构造生产链所需的 AgentServices：LLM / KB 检索 / KB 写库 / Usage 跟踪。

    Args:
        session_factory: DB session 工厂
        llm_factory: 返回 LLMClient 实例的可调用（默认从环境变量选 provider）
        embedding_client_cls: OpenAI Embedding 客户端类（用于构造 EmbeddingService）
        task_writer: 写 task 的 callable（默认 None → dispatch_node 静默跳过落库；
            生产路径传 :class:`matrix.scheduler.db.DbTaskWriter`）
        scheduler: 可选 slot picker；未传时构造默认 :class:`DefaultSlotPicker`
        notifier: Phase 1 反向反馈通道。默认 None → 构造 :class:`WebhookNotifier`：
            写 ``notifications`` 表 + 可选 POST webhook。
            调用方应在 lifespan 收尾 ``await notifier.aclose()`` 关闭长生命周期 httpx client。
    """
    llm = llm_factory()
    if embedding_client_cls is None:
        from matrix.llm.embeddings import EmbeddingClient
        embedding_client_cls = EmbeddingClient
    # 无论 caller 传不传 cls，都从 settings 读 api_key + base_url（硅基流动等需要切换）
    from matrix.config import get_settings

    _emb_settings = get_settings()
    embedder = EmbeddingService(embedding_client_cls(
        api_key=_emb_settings.openai_api_key,
        base_url=_emb_settings.embedding_base_url,
    ))

    if scheduler is None:
        from matrix.scheduler import DefaultSlotPicker
        from matrix.scheduler.round_slot_allocator import (
            DefaultRoundSlotAllocator,
        )

        picker = DefaultSlotPicker(session_factory)
        scheduler = picker
        round_allocator = DefaultRoundSlotAllocator(session_factory)

    from matrix.device.adapters import ApkHttpClient
    from matrix.device.endpoints import DeviceEndpointResolver

    # v0.7+ 第 2 期：装一个进程级 LLM 限速器；并发上限走环境变量 ``MATRIX_LLM_CONCURRENCY``，
    # 缺省 8（典型 tier-1 Provider 撑得住）。
    sem_size = int(os.environ.get("MATRIX_LLM_CONCURRENCY", "8"))
    llm_rate_limiter = LLMRateLimiter(semaphore=asyncio.Semaphore(sem_size))

    if notifier is None:
        # Phase 1：替换原 _noop_notifier。WebhookNotifier 内部持长生命周期 httpx 客户端，
        # lifespan 收尾需 aclose()（app.py 处理）。
        from matrix.agent._notifier_webhook import WebhookNotifier

        notifier = WebhookNotifier(
            session_factory=session_factory,
            config_reader=_LazyConfigReader(session_factory),
        )

    services = build_agent_services(
        llm=llm,
        kb_retriever=_LazyRetriever(session_factory, embedder),
        kb_writer=_LazyWriter(session_factory, embedder),
        device_adapter=ApkHttpClient(resolver=DeviceEndpointResolver(session_factory)),
        config=_LazyConfigReader(session_factory),
        task_writer=task_writer,
        note_writer=db_note_writer,  # v0.7 Phase 5：DRAFT 草稿直接落 notes 表
        scheduler=scheduler,
        notifier=notifier,
        llm_rate_limiter=llm_rate_limiter,
        round_allocator=round_allocator,  # 第 1 期：注入 DefaultRoundSlotAllocator
    )
    return services
