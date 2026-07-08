"""知识库 (kb) 端点。

首次把 KB 暴露为 HTTP 端点（matrix-plan §主题贯穿改造 §4）。

接口：
- GET    /kb/documents              列出文档（按 type/is_published 过滤）
- POST   /kb/documents              创建文档（自动 chunk + embed）
- GET    /kb/documents/{id}         取单条
- PATCH  /kb/documents/{id}         局部更新
- DELETE /kb/documents/{id}         软删
- POST   /kb/documents/{id}/publish  发布（ReviewGate）
- POST   /kb/search                  混合检索（向量+关键词，RRF 融合）

商品库（type=product）走同一套接口。
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    KbDocument,
    KbDocumentCreate,
    KbDocumentListResponse,
    KbDocumentUpdate,
    KbPublishRequest,
    KbPublishResponse,
    KbSearchHit,
    KbSearchRequest,
    KbSearchResponse,
)
from matrix.db.models import KbDocument as KbDocumentORM
from matrix.kb.embedding import EmbeddingService
from matrix.kb.promotion import ReviewGate
from matrix.kb.retrieval import Retriever
from matrix.kb.store import KB_TYPES, KbStore
from matrix.llm.embeddings import EmbeddingClient
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/kb", tags=["kb"])


# ---------------------------------------------------------------------------
# 依赖：构造 KbStore / Retriever（每请求一次，简洁优先；后续可改单例）
# ---------------------------------------------------------------------------


def _get_embedder() -> EmbeddingService:
    """构造 embedding 服务。生产依赖 OPENAI_API_KEY 环境变量。"""
    client = EmbeddingClient()
    return EmbeddingService(client)


def _to_schema(d: KbDocumentORM) -> KbDocument:
    return KbDocument(
        id=d.id,
        type=d.type,  # type: ignore[arg-type]
        ref_id=d.ref_id,
        title=d.title,
        content=d.content,
        metadata=dict(d.metadata_ or {}),
        version=d.version,
        is_published=d.is_published,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


# ---------------------------------------------------------------------------
# 列表 / 取单条
# ---------------------------------------------------------------------------


@router.get("/documents", response_model=KbDocumentListResponse)
async def list_documents(
    type: Optional[str] = Query(None, description="按 type 过滤"),
    is_published: Optional[bool] = Query(None),
    ref_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> KbDocumentListResponse:
    if type is not None and type not in KB_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"invalid type: {type!r}"
        )

    stmt = select(KbDocumentORM).where(KbDocumentORM.deleted_at.is_(None))
    count_stmt = select(func.count(KbDocumentORM.id)).where(
        KbDocumentORM.deleted_at.is_(None)
    )
    if type is not None:
        stmt = stmt.where(KbDocumentORM.type == type)
        count_stmt = count_stmt.where(KbDocumentORM.type == type)
    if is_published is not None:
        stmt = stmt.where(KbDocumentORM.is_published == is_published)
        count_stmt = count_stmt.where(KbDocumentORM.is_published == is_published)
    if ref_id is not None:
        stmt = stmt.where(KbDocumentORM.ref_id == ref_id)
        count_stmt = count_stmt.where(KbDocumentORM.ref_id == ref_id)

    stmt = stmt.order_by(KbDocumentORM.updated_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    total = int((await session.execute(count_stmt)).scalar_one() or 0)
    return KbDocumentListResponse(
        items=[_to_schema(r) for r in rows], total=total
    )


@router.get("/documents/{doc_id}", response_model=KbDocument)
async def get_document(
    doc_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> KbDocument:
    stmt = select(KbDocumentORM).where(
        KbDocumentORM.id == doc_id, KbDocumentORM.deleted_at.is_(None)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "kb document not found")
    return _to_schema(row)


# ---------------------------------------------------------------------------
# 创建 / 更新 / 删除
# ---------------------------------------------------------------------------


@router.post(
    "/documents", response_model=KbDocument, status_code=status.HTTP_201_CREATED
)
async def create_document(
    body: KbDocumentCreate, session: AsyncSession = Depends(get_db)
) -> KbDocument:
    if body.type not in KB_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid type: {body.type!r}; must be one of {sorted(KB_TYPES)}",
        )
    if not body.content or not body.content.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "content must be non-empty"
        )

    store = KbStore(session, _get_embedder())
    doc = await store.create_document(
        type=body.type,
        content=body.content,
        title=body.title,
        ref_id=body.ref_id,
        metadata=body.metadata,
        is_published=body.is_published,
    )
    logger.info(
        "kb.api.create doc_id=%s type=%s published=%s",
        doc.id,
        body.type,
        body.is_published,
    )
    return _to_schema(doc)


@router.patch("/documents/{doc_id}", response_model=KbDocument)
async def update_document(
    doc_id: uuid.UUID,
    body: KbDocumentUpdate,
    session: AsyncSession = Depends(get_db),
) -> KbDocument:
    try:
        store = KbStore(session, _get_embedder())
        doc = await store.update_document(
            doc_id,
            content=body.content,
            title=body.title,
            ref_id=body.ref_id,
            metadata=body.metadata,
            is_published=body.is_published,
        )
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return _to_schema(doc)


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> None:
    store = KbStore(session, _get_embedder())
    ok = await store.soft_delete(doc_id)
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "kb document not found")


# ---------------------------------------------------------------------------
# 发布门禁
# ---------------------------------------------------------------------------


@router.post(
    "/documents/{doc_id}/publish", response_model=KbPublishResponse
)
async def publish_document(
    doc_id: uuid.UUID,
    body: KbPublishRequest,
    session: AsyncSession = Depends(get_db),
) -> KbPublishResponse:
    gate = ReviewGate(session)
    ok = await gate.publish(doc_id, reviewer=body.reviewer, comment=body.comment)
    if not ok:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "publish failed: doc missing, soft-deleted, or already published",
        )
    return KbPublishResponse(doc_id=doc_id, is_published=True)


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


@router.post("/search", response_model=KbSearchResponse)
async def search_kb(
    body: KbSearchRequest, session: AsyncSession = Depends(get_db)
) -> KbSearchResponse:
    if body.type not in KB_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"invalid type: {body.type!r}"
        )
    if not body.query or not body.query.strip():
        return KbSearchResponse(items=[])

    retriever = Retriever(session, _get_embedder())
    try:
        results = await retriever.retrieve(
            body.query,
            type=body.type,
            top_k=body.top_k,
            filters=body.filters,
        )
    except ValueError as e:
        # 白名单外的 filter key
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    return KbSearchResponse(
        items=[
            KbSearchHit(
                chunk_id=r.chunk_id,
                doc_id=r.doc_id,
                doc_type=r.doc_type,  # type: ignore[arg-type]
                doc_title=r.doc_title,
                chunk_index=r.chunk_index,
                text=r.text,
                score=float(r.score),
                sources=list(r.sources),
                metadata=dict(r.metadata or {}),
            )
            for r in results
        ]
    )
