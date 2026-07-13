"""goal_stuck_watchdog 的扫描查询加复合索引

P2-2：``GoalStuckWatchdog`` 每 60s 扫一次 goals，WHERE 子句是
  ``status='active' AND phase='PENDING' AND deleted_at IS NULL
    AND created_at < :cutoff AND phase_updated_at IS NULL``
没有索引时全表扫，10k+ goal 的产线一查就慢。加复合索引
  ``(status, phase, created_at)``：status 是低基数的活跃行首选过滤，
phase 再次（典型的 PENDING 命中最多），created_at 末位直接满足 ORDER BY ASC。

不动老索引（001_initial.py 没给 goals 建过任何索引）。
ORM ``__table_args__`` 不动——项目惯例是索引全部在迁移里。
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "013_goal_phase_index"
down_revision = "012_notes_goal_run_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_goals_status_phase_created "
        "ON goals(status, phase, created_at);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_goals_status_phase_created;")
