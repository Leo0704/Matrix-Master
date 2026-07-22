"""KbStore：知识库文档与 chunk 的 CRUD。

约束：
- 依赖 ``matrix.db.models`` 的 ``KbDocument`` / ``KbChunk``
- 通过构造器注入 ``AsyncSession`` 与 ``EmbeddingService``
- 写操作（``create_document`` / ``update_document``）自动 chunk + embed + 写入 chunks 表
- 软删除：``soft_delete`` 设置 ``deleted_at = NOW()``
- 版本：每次 ``update_document`` 把 ``version`` 字段 +1
- 不直接调 anthropic / openai / pgvector SDK
"""
from __future__ import annotations

from matrix.monitoring.logging import get_logger
import uuid
from typing import Optional

from sqlalchemy import delete as sa_delete, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func as sa_func

from matrix.db.models import KbChunk, KbDocument

from .chunker import Chunker, Chunk
from .embedding import EmbeddingService


logger = get_logger(__name__)


# 允许的 doc.type（agent research/draft/review 节点真查的就是这几个）
# strategy_card 由 analyze 节点提炼 history 写入，draft 节点优先召回。
KB_TYPES: frozenset[str] = frozenset(
    {"brand", "persona", "rule", "topic", "history", "template", "product", "strategy_card", "image_asset"}
)


class KbStore:
    """知识库读写门面。"""

    def __init__(
        self,
        session: AsyncSession,
        embedder: EmbeddingService,
        chunker: Optional[Chunker] = None,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._chunker = chunker or Chunker()

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    async def create_document(
        self,
        *,
        type: str,
        content: str,
        ref_id: Optional[uuid.UUID] = None,
        title: Optional[str] = None,
        metadata: Optional[dict] = None,
        is_published: bool = False,
        business_id: Optional[uuid.UUID] = None,  # v0.7+ 业务归属
    ) -> KbDocument:
        """创建一篇知识库文档，自动 chunk + embed + 写入 chunks。

        Args:
            type: 文档类型（必填，且必须在 ``KB_TYPES`` 内）
            content: 文档正文（必填）
            ref_id: 关联到 ``personas`` / ``topics`` / ``rules`` / ``notes`` 的 ID
            title: 可选标题
            metadata: JSONB metadata
            is_published: 是否已通过 review（默认 False）
            business_id: v0.7+ 业务归属（可选；路由层 POST /kb/documents 已要求必填）
        """
        if type not in KB_TYPES:
            raise ValueError(
                f"invalid kb type: {type!r}, must be one of {sorted(KB_TYPES)}"
            )
        if not content:
            raise ValueError("content must be non-empty")
        meta = dict(metadata or {})

        # 1) 切 chunk
        chunks = self._chunker.split(content)
        if not chunks:
            # 空文本兜底：建一个空 chunk（避免 chunks 表为空导致后续检索无意义）
            chunks = [Chunk(text=content, token_count=0, index=0)]

        # 2) embed chunks
        vectors = await self._embedder.embed_batch([c.text for c in chunks])

        # 3) 文档级 embedding：取 chunks 向量的均值（与 SDD §3.2.2 「embedding 维度 1536」一致）
        doc_vec = _mean_vector(vectors)

        # 4) 写 doc
        doc = KbDocument(
            id=uuid.uuid4(),
            type=type,
            ref_id=ref_id,
            title=title,
            content=content,
            metadata_=meta,
            version=1,
            embedding=doc_vec,
            is_published=is_published,
            business_id=business_id,  # v0.7+ 业务归属
        )
        self._session.add(doc)
        await self._session.flush()  # 拿到 doc.id

        # 5) 写 chunks
        for chunk, vec in zip(chunks, vectors):
            self._session.add(
                KbChunk(
                    id=uuid.uuid4(),
                    doc_id=doc.id,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    embedding=vec,
                )
            )
        await self._session.flush()
        logger.info(
            "kb.create",
            doc_id=doc.id,
            type=type,
            chunks=len(chunks),
            ref_id=ref_id,
        )
        return doc

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    async def get_document(self, doc_id: uuid.UUID) -> Optional[KbDocument]:
        """按 id 取一条文档。已软删的不返回。"""
        stmt = select(KbDocument).where(
            KbDocument.id == doc_id, KbDocument.deleted_at.is_(None)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_documents(
        self,
        *,
        type: Optional[str] = None,
        ref_id: Optional[uuid.UUID] = None,
        is_published: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[KbDocument]:
        """列出文档。可按 type / ref_id / is_published 过滤。"""
        stmt = select(KbDocument).where(KbDocument.deleted_at.is_(None))
        if type is not None:
            stmt = stmt.where(KbDocument.type == type)
        if ref_id is not None:
            stmt = stmt.where(KbDocument.ref_id == ref_id)
        if is_published is not None:
            stmt = stmt.where(KbDocument.is_published == is_published)
        stmt = stmt.order_by(KbDocument.updated_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_chunks(self, doc_id: uuid.UUID) -> list[KbChunk]:
        """取一篇文档的全部 chunk。"""
        stmt = (
            select(KbChunk)
            .where(KbChunk.doc_id == doc_id)
            .order_by(KbChunk.chunk_index)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # update
    # ------------------------------------------------------------------

    async def update_document(
        self,
        doc_id: uuid.UUID,
        *,
        content: Optional[str] = None,
        title: Optional[str] = None,
        metadata: Optional[dict] = None,
        ref_id: Optional[uuid.UUID] = None,
        is_published: Optional[bool] = None,
    ) -> KbDocument:
        """更新文档。

        - ``content`` 变化：重新 chunk + 重新 embed + 替换 chunks；``version += 1``
        - 其它字段：原地修改；``version`` 也 +1（任何 update 都计一版）
        - 未提供的字段保持不变
        """
        doc = await self.get_document(doc_id)
        if doc is None:
            raise LookupError(f"kb document not found: {doc_id}")

        content_changed = content is not None and content != doc.content
        if content is not None:
            doc.content = content
        if title is not None:
            doc.title = title
        if metadata is not None:
            doc.metadata_ = metadata
        if ref_id is not None:
            doc.ref_id = ref_id
        if is_published is not None:
            doc.is_published = is_published
        doc.version = (doc.version or 0) + 1

        if content_changed:
            await self._replace_chunks(doc)

        await self._session.flush()
        logger.info("kb.update", doc_id=doc.id, version=doc.version)
        return doc

    async def _replace_chunks(self, doc: KbDocument) -> None:
        """content 变化时：删旧 chunks + 写新 chunks + 重新计算 doc.embedding。"""
        # 删旧 chunks
        await self._session.execute(
            sa_delete(KbChunk).where(KbChunk.doc_id == doc.id)
        )

        # 切新 chunks
        chunks = self._chunker.split(doc.content)
        if not chunks:
            chunks = [Chunk(text=doc.content, token_count=0, index=0)]
        vectors = await self._embedder.embed_batch([c.text for c in chunks])

        # 写新 chunks
        for chunk, vec in zip(chunks, vectors):
            self._session.add(
                KbChunk(
                    id=uuid.uuid4(),
                    doc_id=doc.id,
                    chunk_index=chunk.index,
                    text=chunk.text,
                    token_count=chunk.token_count,
                    embedding=vec,
                )
            )

        # 更新 doc 级 embedding
        doc.embedding = _mean_vector(vectors)

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------

    async def soft_delete(self, doc_id: uuid.UUID) -> bool:
        """软删除：设置 ``deleted_at = NOW()``，并把 ``is_published`` 置 False。

        Returns:
            是否真的删了一条（False 表示 doc 不存在或已软删）
        """
        doc = await self.get_document(doc_id)
        if doc is None:
            return False
        await self._session.execute(
            sa_update(KbDocument)
            .where(KbDocument.id == doc_id)
            .values(
                deleted_at=sa_func.now(),
                is_published=False,
            )
        )
        await self._session.flush()
        logger.info("kb.soft_delete", doc_id=doc_id)
        return True

    async def hard_delete(self, doc_id: uuid.UUID) -> bool:
        """物理删除（级联删 chunks）。仅供测试 / 管理脚本使用。"""
        doc = await self.get_document(doc_id)
        if doc is None:
            return False
        # chunks 由 ON DELETE CASCADE 在物理删 doc 时连带删
        await self._session.execute(
            sa_delete(KbDocument).where(KbDocument.id == doc_id)
        )
        await self._session.flush()
        logger.info("kb.hard_delete", doc_id=doc_id)
        return True


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    """多向量按元素求均值。空列表返回空列表。"""
    if not vectors:
        return []
    dim = len(vectors[0])
    if dim == 0:
        return []
    sums = [0.0] * dim
    for v in vectors:
        for i, x in enumerate(v):
            sums[i] += float(x)
    return [s / len(vectors) for s in sums]


__all__ = ["KB_TYPES", "KbStore"]
