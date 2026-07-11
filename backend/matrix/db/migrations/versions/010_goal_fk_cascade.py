"""把 goal_id 的 FK 改成 ON DELETE CASCADE

v0.7 硬删 goal：ORM 模型早就声明了 ``ondelete='CASCADE'``，但老 migration
没把 DDL 同步进 PG，硬删 goal 时被 FK 约束拒。修一下：

- agent_runs.goal_id    → CASCADE（删 goal 自动清跑过的 run 痕迹）
- goal_rounds.goal_id   → CASCADE
- plans.goal_id          → CASCADE（plans 走 agent_runs 的 CASCADE 也行，但 plan 也 CASCADE 更干净）
- tasks.plan_id         → 已有 CASCADE（前面某个 migration 加的，保留）

注意：notes 不 FK goal，删 goal 不会带笔记；accounts/devices 也不 FK goal。
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "010_goal_fk_cascade"
down_revision = "009_goal_tuning_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 三个表 + 三个 FK 名字（按 SQLAlchemy 自动生成约定 fk__<table>__<column>__<ref>）
    # 但具体名字可能不一样，先用通用方式：drop + add
    # 找现有 FK constraint 名字，drop，然后 add with CASCADE

    # agent_runs.goal_id
    op.execute("""
        DO $$
        DECLARE
            fk_name text;
        BEGIN
            SELECT conname INTO fk_name
            FROM pg_constraint
            WHERE conrelid = 'agent_runs'::regclass
              AND contype = 'f'
              AND pg_get_constraintdef(oid) LIKE '%goals%';
            IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE agent_runs DROP CONSTRAINT %I', fk_name);
            END IF;
        END$$;
    """)
    op.execute(
        "ALTER TABLE agent_runs "
        "ADD CONSTRAINT agent_runs_goal_id_fkey "
        "FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE;"
    )

    # goal_rounds.goal_id
    op.execute("""
        DO $$
        DECLARE
            fk_name text;
        BEGIN
            SELECT conname INTO fk_name
            FROM pg_constraint
            WHERE conrelid = 'goal_rounds'::regclass
              AND contype = 'f'
              AND pg_get_constraintdef(oid) LIKE '%goals%';
            IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE goal_rounds DROP CONSTRAINT %I', fk_name);
            END IF;
        END$$;
    """)
    op.execute(
        "ALTER TABLE goal_rounds "
        "ADD CONSTRAINT goal_rounds_goal_id_fkey "
        "FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE;"
    )

    # plans.goal_id
    op.execute("""
        DO $$
        DECLARE
            fk_name text;
        BEGIN
            SELECT conname INTO fk_name
            FROM pg_constraint
            WHERE conrelid = 'plans'::regclass
              AND contype = 'f'
              AND pg_get_constraintdef(oid) LIKE '%goals%';
            IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE plans DROP CONSTRAINT %I', fk_name);
            END IF;
        END$$;
    """)
    op.execute(
        "ALTER TABLE plans "
        "ADD CONSTRAINT plans_goal_id_fkey "
        "FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE;"
    )


def downgrade() -> None:
    # 回滚：去掉 CASCADE（变回默认 NO ACTION）
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_goal_id_fkey;")
    op.execute(
        "ALTER TABLE agent_runs "
        "ADD CONSTRAINT agent_runs_goal_id_fkey "
        "FOREIGN KEY (goal_id) REFERENCES goals(id);"
    )
    op.execute("ALTER TABLE goal_rounds DROP CONSTRAINT IF EXISTS goal_rounds_goal_id_fkey;")
    op.execute(
        "ALTER TABLE goal_rounds "
        "ADD CONSTRAINT goal_rounds_goal_id_fkey "
        "FOREIGN KEY (goal_id) REFERENCES goals(id);"
    )
    op.execute("ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_goal_id_fkey;")
    op.execute(
        "ALTER TABLE plans "
        "ADD CONSTRAINT plans_goal_id_fkey "
        "FOREIGN KEY (goal_id) REFERENCES goals(id);"
    )
