"""Review gate：知识库文档的发布 / 撤回（kb-writing-guide §4.5）。

约束：
- persona / rule 未经 review 不可被 Agent 检索到
- publish / unpublish 翻转 ``kb_documents.is_published``
- 实际 review 多人签（运营 / 产品 / 安全）的流程由上层业务逻辑把控；
  本类只负责状态翻转 + 留痕
- 留痕写入 ``audit_logs`` 表（不新增表，与 ``kb-writing-guide §4.5`` 中
  「``kb_review_log`` 表待 schema 补充」保持一致；待 schema 补充后再迁移）
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.models import AuditLog, KbDocument


logger = logging.getLogger(__name__)


class ReviewGate:
    """KB 文档发布状态门禁。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    async def is_published(self, doc_id: uuid.UUID) -> bool:
        """``doc_id`` 当前是否已发布。已软删 / 不存在 → False。"""
        stmt = select(KbDocument.is_published).where(
            KbDocument.id == doc_id,
            KbDocument.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        value = result.scalar_one_or_none()
        return bool(value)

    async def get_publish_state(self, doc_id: uuid.UUID) -> Optional[bool]:
        """与 ``is_published`` 一样，但 doc 不存在时返回 None。"""
        stmt = select(KbDocument.is_published).where(
            KbDocument.id == doc_id,
            KbDocument.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # mutations
    # ------------------------------------------------------------------

    async def publish(
        self,
        doc_id: uuid.UUID,
        reviewer: str,
        *,
        comment: Optional[str] = None,
    ) -> bool:
        """发布（reviewer 已批准）。

        Args:
            doc_id: 文档 id
            reviewer: 审核人标识（用户名 / UUID）
            comment: 可选审核意见

        Returns:
            True 表示状态从 unpublished → published
            False 表示 doc 不存在、已软删、或者当前已发布（幂等）
        """
        if not reviewer:
            raise ValueError("reviewer is required")

        doc = await self._get_live_doc(doc_id)
        if doc is None:
            return False
        if doc.is_published:
            return False  # 幂等：已发布不重复写审计

        from sqlalchemy import update as sa_update

        await self._session.execute(
            sa_update(KbDocument)
            .where(KbDocument.id == doc_id)
            .values(is_published=True)
        )
        await self._write_audit(
            doc_id=doc_id,
            reviewer=reviewer,
            decision="approve",
            comment=comment,
            new_state=True,
        )
        await self._session.flush()
        logger.info("kb.publish doc_id=%s reviewer=%s", doc_id, reviewer)
        return True

    async def unpublish(
        self,
        doc_id: uuid.UUID,
        reviewer: str,
        *,
        comment: Optional[str] = None,
    ) -> bool:
        """撤回发布（rollback review / 临时下架）。

        Returns:
            True 表示状态从 published → unpublished
            False 表示 doc 不存在、已软删、或者当前未发布（幂等）
        """
        if not reviewer:
            raise ValueError("reviewer is required")

        doc = await self._get_live_doc(doc_id)
        if doc is None:
            return False
        if not doc.is_published:
            return False  # 幂等：未发布不重复写审计

        from sqlalchemy import update as sa_update

        await self._session.execute(
            sa_update(KbDocument)
            .where(KbDocument.id == doc_id)
            .values(is_published=False)
        )
        await self._write_audit(
            doc_id=doc_id,
            reviewer=reviewer,
            decision="unpublish",
            comment=comment,
            new_state=False,
        )
        await self._session.flush()
        logger.info("kb.unpublish doc_id=%s reviewer=%s", doc_id, reviewer)
        return True

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _get_live_doc(self, doc_id: uuid.UUID) -> Optional[KbDocument]:
        stmt = select(KbDocument).where(
            KbDocument.id == doc_id,
            KbDocument.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _write_audit(
        self,
        *,
        doc_id: uuid.UUID,
        reviewer: str,
        decision: str,
        comment: Optional[str],
        new_state: bool,
    ) -> None:
        """把 review 行为写入 ``audit_logs``。

        action 约定：
        - ``kb.publish`` — 发布（new_state=True）
        - ``kb.unpublish`` — 撤回（new_state=False）
        """
        action = "kb.publish" if new_state else "kb.unpublish"
        payload = {
            "doc_id": str(doc_id),
            "reviewer": reviewer,
            "decision": decision,
            "comment": comment,
            "new_state": new_state,
            "ts": datetime.utcnow().isoformat() + "Z",
        }
        await self._session.execute(
            insert(AuditLog).values(
                user_id=reviewer,
                action=action,
                resource_type="kb_document",
                resource_id=doc_id,
                after_state=payload,
            )
        )


__all__ = ["ReviewGate"]
