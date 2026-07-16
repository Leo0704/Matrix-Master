"""复盘 + 学习端点（第 3 期）。

- POST /learning/summarize-goal/{goal_id}  触发复盘，把 goal 的 run 数据 → KB
- GET  /learning/documents?type=strategy_card  列历史复盘（viral + 避坑）
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.agent.summarize import summarize_goal_to_kb
from matrix.api.deps import get_db
from matrix.api.schemas import (
    KbDocument as KbDocumentSchema,
    KbDocumentListResponse,
)
from matrix.db.models import KbDocument as KbDocumentORM
from matrix.kb._singleton import get_embedder as _get_global_embedder
from matrix.kb.embedding import EmbeddingService
from matrix.llm.embeddings import EmbeddingClient
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/learning", tags=["learning"])


# ---------------------------------------------------------------------------
# 依赖
# ---------------------------------------------------------------------------


async def _get_embedder() -> EmbeddingService:
    """复用 kb 路由的 embedder 单例（同样从 settings 读硅基流动等）。"""
    from matrix.config import get_settings

    s = get_settings()
    return await _get_global_embedder(
        EmbeddingClient,
        api_key=s.openai_api_key,
        base_url=s.embedding_base_url,
    )


def _to_schema(d: KbDocumentORM) -> KbDocumentSchema:
    return KbDocumentSchema(
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
# POST /learning/summarize-goal/{goal_id}
# ---------------------------------------------------------------------------


@router.post(
    "/summarize-goal/{goal_id}",
    response_model=KbDocumentListResponse,
    status_code=status.HTTP_201_CREATED,
)
async def trigger_summarize(
    goal_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> KbDocumentListResponse:
    """触发复盘：把 goal 的所有 run 数据喂给 LLM，提炼爆款/失败，写到 KB（默认未发布）。"""
    try:
        embedder = await _get_embedder()
    except Exception as exc:
        logger.exception("learning.embedder_unavailable")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"embedding service unavailable: {exc}",
        ) from exc

    docs = await summarize_goal_to_kb(session, embedder, goal_id)
    await session.commit()
    return KbDocumentListResponse(
        items=[_to_schema(d) for d in docs],
        total=len(docs),
    )


# ---------------------------------------------------------------------------
# GET /learning/documents
# ---------------------------------------------------------------------------


@router.get("/documents", response_model=KbDocumentListResponse)
async def list_learning_documents(
    type: Optional[str] = Query(
        None, description="strategy_card / rule / brand / persona / history"
    ),
    is_published: Optional[bool] = Query(None),
    business_id: Optional[uuid.UUID] = Query(None, description="v0.7+ 业务过滤"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> KbDocumentListResponse:
    """列历史复盘文档。"""
    stmt = select(KbDocumentORM).where(KbDocumentORM.deleted_at.is_(None))
    if type is not None:
        stmt = stmt.where(KbDocumentORM.type == type)
    if is_published is not None:
        stmt = stmt.where(KbDocumentORM.is_published == is_published)
    if business_id is not None:
        stmt = stmt.where(KbDocumentORM.business_id == business_id)
    stmt = stmt.order_by(KbDocumentORM.updated_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    return KbDocumentListResponse(
        items=[_to_schema(r) for r in rows],
        total=len(rows),
    )


__all__ = ["router"]
