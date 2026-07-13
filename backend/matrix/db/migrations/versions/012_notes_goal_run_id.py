"""notes 表加 goal_id / run_id 两个外键，替换"5 分钟时间窗"的笔记归属猜测

P1-2：当前 ``_gather_round_kpi``（orchestrator）和 ``_load_goal_snapshot``
（summarize）都是借 Note.created_at 和 AgentRun.started_at 之间 ±5 min 的
时间窗把笔记挂回 run / goal。两个 run 间隔 < 5 分钟就会撞笔记。

加两列 + 复合索引：DRAFT/PUBLISH 节点往后写 notes 时把 ``state['goal_id']`` /
``state['run_id']`` 透传到 ``note_writer``；新查询 ``WHERE notes.run_id = run.id``
直接命中。旧数据 ``run_id IS NULL`` 仍走时间窗回退（保留兼容）。

ON DELETE SET NULL：删 goal 不带笔记（保留已发布的内容），单 run 异常删也不孤儿化 note。
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "012_notes_goal_run_id"
down_revision = "011_agent_runs_round_number"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 两个 nullable FK，ON DELETE SET NULL
    op.execute(
        "ALTER TABLE notes "
        "ADD COLUMN IF NOT EXISTS goal_id UUID "
        "REFERENCES goals(id) ON DELETE SET NULL;"
    )
    op.execute(
        "ALTER TABLE notes "
        "ADD COLUMN IF NOT EXISTS run_id UUID "
        "REFERENCES agent_runs(id) ON DELETE SET NULL;"
    )

    # 索引：notes.run_id 让 _gather_round_kpi 的直查命中
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_run_id "
        "ON notes(run_id) WHERE run_id IS NOT NULL;"
    )
    # 索引：(goal_id, run_id) 让按轮次拉 notes + goal 维度分析都能用
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notes_goal_run "
        "ON notes(goal_id, run_id) WHERE goal_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_notes_goal_run;")
    op.execute("DROP INDEX IF EXISTS idx_notes_run_id;")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS run_id;")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS goal_id;")
