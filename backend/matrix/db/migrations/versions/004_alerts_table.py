"""add alerts table

主题贯穿改造 §11：/alerts 端点对应的 DB 表。
告警来自 monitoring/alerts.py 9 条 check 规则的扫描结果。

Revision ID: 004_alerts_table
Revises: 003_kb_product_type
Create Date: 2026-07-09 13:00:00
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "004_alerts_table"
down_revision = "003_kb_product_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            code            VARCHAR(64) NOT NULL,
            severity        VARCHAR(16) NOT NULL
                                CHECK (severity IN ('critical', 'warning', 'info')),
            message         TEXT NOT NULL,
            subject_id      VARCHAR(128),
            resolved        BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at     TIMESTAMPTZ
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alerts_unresolved
            ON alerts(created_at DESC) WHERE resolved = FALSE;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_alerts_code
            ON alerts(code, created_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_alerts_code;")
    op.execute("DROP INDEX IF EXISTS idx_alerts_unresolved;")
    op.execute("DROP TABLE IF EXISTS alerts;")
