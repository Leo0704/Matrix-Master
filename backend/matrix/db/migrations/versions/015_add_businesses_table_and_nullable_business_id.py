"""add businesses table + nullable business_id on 7 core tables

业务模型重构第 1 期：业务是项目根，所有核心资源（devices/accounts/personas/
goals/notes/kb_documents/agent_runs）挂在业务名下。

第 015 步只加表 + 加 nullable 列，不加 FK（FK 在 017 升 NOT NULL 时再加）。
回填在独立脚本 scripts/backfill_business.py（不放 alembic）。

Revision ID: 015_add_businesses_table_and_nullable_business_id
Revises: f1e2d3c4b5a6
Create Date: 2026-07-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015_add_businesses_table_and_nullable_business_id"
down_revision: Union[str, None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 创建 businesses 表
    op.execute(
        """
        CREATE TABLE businesses (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            name VARCHAR(64) NOT NULL,
            slug VARCHAR(64) NOT NULL UNIQUE,
            description TEXT,
            status VARCHAR(16) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            archived_at TIMESTAMPTZ,
            CONSTRAINT businesses_status_check
                CHECK (status IN ('active', 'archived'))
        )
        """
    )
    # 索引：list 接口按 status 过滤 + 按 slug 查（slug 已 UNIQUE，自动建索引）
    op.execute("CREATE INDEX idx_businesses_status ON businesses(status)")

    # 2. 7 张表加 business_id UUID 列（全部 NULLABLE，无 FK）
    #    017 migration 会升 NOT NULL + 加 FK + 加复合索引 + 切 Persona UNIQUE 约束
    tables = [
        "devices",
        "accounts",
        "personas",
        "goals",
        "notes",
        "kb_documents",
        "agent_runs",
    ]
    for table in tables:
        op.execute(f"ALTER TABLE {table} ADD COLUMN business_id UUID")


def downgrade() -> None:
    # 反向：DROP COLUMN 再 DROP TABLE
    tables = [
        "devices",
        "accounts",
        "personas",
        "goals",
        "notes",
        "kb_documents",
        "agent_runs",
    ]
    for table in tables:
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS business_id")

    op.execute("DROP INDEX IF EXISTS idx_businesses_status")
    op.execute("DROP TABLE IF EXISTS businesses")