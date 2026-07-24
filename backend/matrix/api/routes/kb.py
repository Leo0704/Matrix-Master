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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db, resolve_active_business
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
    ViralIngestRequest,
    ViralIngestResponse,
)
from matrix.db.models import KbDocument as KbDocumentORM
from matrix.kb._singleton import get_embedder
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


async def _get_embedder() -> EmbeddingService:
    """返回进程级缓存的 embedding 服务。从 settings 读 api_key + base_url（硅基流动等 OpenAI 兼容服务）。"""
    from matrix.config import get_settings

    s = get_settings()
    return await get_embedder(
        EmbeddingClient,
        api_key=s.openai_api_key,
        base_url=s.embedding_base_url,
    )


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
        business_id=d.business_id,  # v0.7+ 业务归属
    )


# ---------------------------------------------------------------------------
# 列表 / 取单条
# ---------------------------------------------------------------------------


@router.get("/documents", response_model=KbDocumentListResponse)
async def list_documents(
    type: Optional[str] = Query(None, description="按 type 过滤"),
    is_published: Optional[bool] = Query(None),
    ref_id: Optional[uuid.UUID] = Query(None),
    business_id: Optional[uuid.UUID] = Query(None, description="v0.7+ 业务过滤"),
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
    if business_id is not None:
        stmt = stmt.where(KbDocumentORM.business_id == business_id)
        count_stmt = count_stmt.where(KbDocumentORM.business_id == business_id)

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
    # v0.7+ 业务模型重构：业务上下文校验（存在 + active）
    await resolve_active_business(session, body.business_id)

    if body.type not in KB_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid type: {body.type!r}; must be one of {sorted(KB_TYPES)}",
        )
    if not body.content or not body.content.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "content must be non-empty"
        )

    store = KbStore(session, await _get_embedder())
    doc = await store.create_document(
        type=body.type,
        content=body.content,
        title=body.title,
        ref_id=body.ref_id,
        metadata=body.metadata,
        is_published=body.is_published,
        business_id=body.business_id,  # v0.7+ 业务归属
    )
    logger.info(
        "kb.api.create",
        doc_id=doc.id,
        type=body.type,
        published=body.is_published,
    )
    return _to_schema(doc)


@router.post(
    "/ingest-viral",
    response_model=ViralIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_viral(
    body: ViralIngestRequest, session: AsyncSession = Depends(get_db)
) -> ViralIngestResponse:
    """粘贴一篇爆款原文 → LLM 拆解 → 写 KB。

    存一条 ``history``（直接发布，供 AI 检索参考）+ 可选一张 ``strategy_card``
    （草稿，人工发布后 draft 才召回）。
    """
    if not body.raw_text or not body.raw_text.strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "raw_text must be non-empty"
        )
    # 业务上下文校验（存在 + active）
    await resolve_active_business(session, body.business_id)

    try:
        embedder = await _get_embedder()
    except Exception as exc:
        logger.exception("kb.ingest_viral.embedder_unavailable")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"embedding service unavailable: {exc}",
        ) from exc

    from matrix.agent.ingest_viral import ingest_viral_text_to_kb

    history_doc, card_pending = await ingest_viral_text_to_kb(
        session,
        embedder,
        raw_text=body.raw_text,
        title=body.title,
        metrics=body.metrics,
        business_id=body.business_id,
    )
    await session.commit()
    logger.info(
        "kb.api.ingest_viral",
        doc_id=history_doc.id,
        strategy_card_pending=card_pending,
    )
    return ViralIngestResponse(
        history=_to_schema(history_doc), strategy_card_pending=card_pending
    )


@router.post(
    "/documents/upload", response_model=KbDocument, status_code=status.HTTP_201_CREATED
)
async def upload_document(
    file: UploadFile = File(...),
    type: str = Form(...),
    title: Optional[str] = Form(None),
    is_published: bool = Form(True),
    business_id: uuid.UUID = Form(...),
    session: AsyncSession = Depends(get_db),
) -> KbDocument:
    """拖文件上传：支持 .md / .txt。文件内容作为正文，标题默认用文件名（去后缀）。

    ``business_id`` 必填（kb_documents.business_id 是 NOT NULL，
    与 POST /kb/documents 一致），传入后校验业务上下文。
    """
    if type not in KB_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"invalid type: {type!r}; must be one of {sorted(KB_TYPES)}",
        )
    await resolve_active_business(session, business_id)
    suffix = (file.filename or "").lower().rsplit(".", 1)[-1] if file.filename else ""
    if suffix not in ("md", "txt"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unsupported file type: .{suffix}; only .md / .txt",
        )
    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content = raw.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "file is not utf-8 / gbk text",
            )
    if not content.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is empty")
    if not title:
        title = (file.filename or "未命名").rsplit(".", 1)[0]

    store = KbStore(session, await _get_embedder())
    doc = await store.create_document(
        type=type, content=content, title=title, is_published=is_published,
        business_id=business_id,  # 业务归属（必填，NOT NULL）
    )
    logger.info("kb.api.upload", doc_id=doc.id, type=type, filename=file.filename)
    return _to_schema(doc)


@router.patch("/documents/{doc_id}", response_model=KbDocument)
async def update_document(
    doc_id: uuid.UUID,
    body: KbDocumentUpdate,
    session: AsyncSession = Depends(get_db),
) -> KbDocument:
    try:
        store = KbStore(session, await _get_embedder())
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
    store = KbStore(session, await _get_embedder())
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

    retriever = Retriever(session, await _get_embedder())
    try:
        results = await retriever.retrieve(
            body.query,
            type=body.type,
            top_k=body.top_k,
            filters=body.filters,
            business_id=body.business_id,
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
