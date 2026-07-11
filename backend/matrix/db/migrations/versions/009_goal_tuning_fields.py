"""Goal 可调字段：让老板创建 goal 时传 target_likes / notes_per_round

v0.7 第 1 期"中控运营"硬编码修复：之前 KPI 阈值（500 赞）和每轮
notes 数（3 篇）都是模块常量，老板设目标时不能调。现在改用 goal
表字段，缺省回退到原常量。
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "009_goal_tuning_fields"
down_revision = "008_goal_orchestrator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS target_likes INTEGER NOT NULL DEFAULT 500;")
    op.execute("ALTER TABLE goals ADD COLUMN IF NOT EXISTS notes_per_round INTEGER NOT NULL DEFAULT 3;")
    # notes_per_round 范围 1~20 合理
    op.execute(
        "ALTER TABLE goals ADD CONSTRAINT goals_notes_per_round_range_check "
        "CHECK (notes_per_round BETWEEN 1 AND 20);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE goals DROP CONSTRAINT IF EXISTS goals_notes_per_round_range_check;")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS notes_per_round;")
    op.execute("ALTER TABLE goals DROP COLUMN IF EXISTS target_likes;")
