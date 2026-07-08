"""add is_published to kb_documents

kb-writing-guide §4.5 评审流程：
- 未经 review 完成的 persona / rule 不可被 Agent 检索到
- 在 kb_documents.is_published 字段标记

Revision ID: 002_add_kb_review
Revises: 001_initial
Create Date: 2026-07-08 12:00:00
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "002_add_kb_review"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS 防御：万一 001_initial 后续版本已经加了该列也不会报错
    op.execute(
        """
        ALTER TABLE kb_documents
            ADD COLUMN IF NOT EXISTS is_published BOOLEAN NOT NULL DEFAULT FALSE;
        """
    )
    # 列出未发布的 doc（review 状态），便于运维追查
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_kb_documents_published
            ON kb_documents(is_published) WHERE deleted_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_kb_documents_published;")
    op.execute("ALTER TABLE kb_documents DROP COLUMN IF EXISTS is_published;")
