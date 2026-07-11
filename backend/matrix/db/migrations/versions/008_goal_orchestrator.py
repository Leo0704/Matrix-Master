"""Goal orchestrator：给 goal 加 phase 状态机字段 + goal_rounds 表

第 1 期"中控运营"：1 个 goal 不再是 1 个 run = 1 篇稿，而是多轮
（拆任务 → 跑 task → 看数据 → 复盘 → 续跑/收工）的运营周期。

新增字段：
- goals.phase：当前 phase（PENDING / PREPARING / EXECUTING / MONITORING /
  SUMMARIZING / DECIDING / DONE）
- goals.current_round：当前是第几轮（从 1 开始）
- goals.max_rounds：最多跑几轮（默认 3）
- goals.learning_summary：每轮 LLM 写的复盘（最新一轮）

新表 goal_rounds：每轮一条记录，含 KPI 汇总、起止时间。
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "008_goal_orchestrator"
down_revision = "007_unique_device_per_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) goals 表加 4 字段
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS phase VARCHAR(32) NOT NULL DEFAULT 'PENDING';")
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS current_round INTEGER NOT NULL DEFAULT 1;")
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS max_rounds INTEGER NOT NULL DEFAULT 3;")
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS learning_summary TEXT;")
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS phase_updated_at TIMESTAMPTZ;")
    # CHECK constraint
    op.execute(
        "ALTER TABLE goals ADD CONSTRAINT goals_phase_check "
        "CHECK (phase IN ('PENDING','PREPARING','EXECUTING','MONITORING',"
        "'SUMMARIZING','DECIDING','DONE'));"
    )

    # 2) 新表 goal_rounds
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS goal_rounds (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            round_number INTEGER NOT NULL,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMPTZ,
            kpi_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
            notes_created INTEGER NOT NULL DEFAULT 0,
            total_views INTEGER NOT NULL DEFAULT 0,
            total_likes INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(goal_id, round_number)
        );
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_goal_rounds_goal ON goal_rounds(goal_id);")
    op.execute("CREATE INDEX IF NOT EXISTS idx_goal_rounds_active ON goal_rounds(goal_id) WHERE ended_at IS NULL;")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS goal_rounds;")
    op.execute("ALTER TABLE goals DROP CONSTRAINT IF EXISTS goals_phase_check;")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS phase_updated_at;")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS learning_summary;")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS max_rounds;")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS current_round;")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS phase;")
