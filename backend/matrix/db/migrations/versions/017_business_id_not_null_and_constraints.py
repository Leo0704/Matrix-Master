"""business_id NOT NULL + FK + 复合索引 + Persona UNIQUE 切换

业务模型重构第 1 期的最后一步：
- 前置门：scripts/backfill_business.py --verify 通过（7 表 business_id NULL = 0）
- 不可逆：017 跑完降级会丢数据完整性约束

变更：
1) 7 张表 ALTER COLUMN business_id SET NOT NULL
2) 7 个 FK：REFERENCES businesses(id) ON DELETE RESTRICT（业务永不物理删）
3) 6 个复合索引（按文档 16 节）：
   - idx_goals_business_phase ON goals(business_id, phase) WHERE deleted_at IS NULL
   - idx_notes_business_created ON notes(business_id, created_at DESC) WHERE deleted_at IS NULL
   - idx_kb_documents_business_type ON kb_documents(business_id, type) WHERE deleted_at IS NULL
   - idx_accounts_business_status ON accounts(business_id, status) WHERE deleted_at IS NULL
   - idx_devices_business_status ON devices(business_id, status) WHERE deleted_at IS NULL
   - idx_agent_runs_business_goal_round ON agent_runs(business_id, goal_id, round_number)
4) Persona UNIQUE 切换：UNIQUE(name) → UNIQUE(business_id, name)（跨业务允许重名）

Revision ID: 017_business_id_not_null_and_constraints
Revises: 015_add_businesses_table_and_nullable_business_id
Create Date: 2026-07-17

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "017_business_id_not_null_and_constraints"
down_revision: Union[str, None] = "015_add_businesses_table_and_nullable_business_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 7 张表统一处理
TABLES = [
    "devices",
    "accounts",
    "personas",
    "goals",
    "notes",
    "kb_documents",
    "agent_runs",
]


def upgrade() -> None:
    # 1) 7 张表 SET NOT NULL（前置门：backfill --verify 全 0）
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN business_id SET NOT NULL")

    # 2) 7 个 FK：ON DELETE RESTRICT（业务永不物理删，软归档是 status 字段）
    for table in TABLES:
        # IF EXISTS 防止重跑
        op.execute(
            f"ALTER TABLE {table} "
            f"DROP CONSTRAINT IF EXISTS {table}_business_id_fkey"
        )
        op.execute(
            f"ALTER TABLE {table} "
            f"ADD CONSTRAINT {table}_business_id_fkey "
            f"FOREIGN KEY (business_id) REFERENCES businesses(id) "
            f"ON DELETE RESTRICT"
        )

    # 3) 6 个复合索引
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_goals_business_phase "
        "ON goals(business_id, phase) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_business_created "
        "ON notes(business_id, created_at DESC) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_kb_documents_business_type "
        "ON kb_documents(business_id, type) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_accounts_business_status "
        "ON accounts(business_id, status) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_devices_business_status "
        "ON devices(business_id, status) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_business_goal_round "
        "ON agent_runs(business_id, goal_id, round_number)"
    )

    # 4) Persona UNIQUE 切换：UNIQUE(name) → UNIQUE(business_id, name)
    op.execute("ALTER TABLE personas DROP CONSTRAINT IF EXISTS personas_name_key")
    op.execute(
        "ALTER TABLE personas "
        "ADD CONSTRAINT personas_business_id_name_key "
        "UNIQUE (business_id, name)"
    )


def downgrade() -> None:
    # 反向：Persona UNIQUE 切回
    op.execute(
        "ALTER TABLE personas DROP CONSTRAINT IF EXISTS personas_business_id_name_key"
    )
    op.execute(
        "ALTER TABLE personas "
        "ADD CONSTRAINT personas_name_key UNIQUE (name)"
    )

    # 6 个索引 DROP
    for idx in [
        "idx_goals_business_phase",
        "idx_notes_business_created",
        "idx_kb_documents_business_type",
        "idx_accounts_business_status",
        "idx_devices_business_status",
        "idx_agent_runs_business_goal_round",
    ]:
        op.execute(f"DROP INDEX IF EXISTS {idx}")

    # 7 个 FK DROP
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} "
            f"DROP CONSTRAINT IF EXISTS {table}_business_id_fkey"
        )

    # 7 张表 DROP NOT NULL
    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN business_id DROP NOT NULL"
        )