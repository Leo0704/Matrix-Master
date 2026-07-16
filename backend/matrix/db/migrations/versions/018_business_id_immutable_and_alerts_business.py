"""business_id 不可改 trigger + alerts 加 business_id 列（v0.7+ 业务模型重构）

按文档第 4 期可选增强：
1) DB 层 trigger：BEFORE UPDATE ON 7 张核心表抛异常，确保业务创建后不可改
2) alerts 表加 business_id UUID 列（015 漏掉；这里补上，与 notifications 对齐）
3) 6 个 FK ON DELETE RESTRICT（017 已加 alerts 不在范围 → 这里补 alerts FK）
4) 1 个复合索引（alerts.business_id, severity WHERE resolved=false）

注：7 张核心表的 business_id NOT NULL 已由 017 升完；本文不再重复。

Revision ID: 018_business_id_immutable_and_alerts_business
Revises: 017_business_id_not_null_and_constraints
Create Date: 2026-07-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "018_business_id_immutable_and_alerts_business"
down_revision: Union[str, None] = "017_business_id_not_null_and_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 7 张核心表：业务创建后不可改
TABLES_WITH_BUSINESS_ID = [
    "devices",
    "accounts",
    "personas",
    "goals",
    "notes",
    "kb_documents",
    "agent_runs",
]


def upgrade() -> None:
    # 1) 7 张核心表加 trigger：业务创建后不可改（任何 UPDATE business_id 都抛异常）
    for table in TABLES_WITH_BUSINESS_ID:
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {table}_business_id_immutable()
            RETURNS TRIGGER AS $$
            BEGIN
                IF NEW.business_id IS DISTINCT FROM OLD.business_id THEN
                    RAISE EXCEPTION
                        'business_id is immutable on {table} (id=%, old=%, new=%)',
                        OLD.id, OLD.business_id, NEW.business_id
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_business_id_immutable ON {table}"
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_business_id_immutable
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION {table}_business_id_immutable();
            """
        )

    # 2) alerts 表加 business_id UUID 列（nullable，预留历史告警无业务归属的情况）
    op.execute("ALTER TABLE alerts ADD COLUMN IF NOT EXISTS business_id UUID")

    # 3) alerts 表加 FK：REFERENCES businesses(id) ON DELETE RESTRICT
    op.execute(
        "ALTER TABLE alerts "
        "DROP CONSTRAINT IF EXISTS alerts_business_id_fkey"
    )
    op.execute(
        "ALTER TABLE alerts "
        "ADD CONSTRAINT alerts_business_id_fkey "
        "FOREIGN KEY (business_id) REFERENCES businesses(id) "
        "ON DELETE RESTRICT"
    )

    # 4) alerts 加复合索引（partial：仅未解决的告警）
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_alerts_business_severity "
        "ON alerts(business_id, severity) WHERE resolved = false"
    )


def downgrade() -> None:
    # 反向：删 trigger → 删 alerts 列/FK/索引
    for table in TABLES_WITH_BUSINESS_ID:
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_business_id_immutable ON {table}"
        )
        op.execute(
            f"DROP FUNCTION IF EXISTS {table}_business_id_immutable()"
        )

    op.execute("DROP INDEX IF EXISTS idx_alerts_business_severity")
    op.execute(
        "ALTER TABLE alerts DROP CONSTRAINT IF EXISTS alerts_business_id_fkey"
    )
    op.execute("ALTER TABLE alerts DROP COLUMN IF EXISTS business_id")