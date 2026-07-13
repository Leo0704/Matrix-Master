"""agent_runs 加 round_number 真列，替换 JSONB 内嵌字段

P0-2：当前 ``round_number`` 只存在 ``agent_runs.payload->>'round_number'``
的 JSONB 路径里，orchestrator 每次按轮次查的时候都做 ``cast(payload['...'].astext, Integer)``，
索引不了，跑大表会扫。把字段提升成一等列，老 JSONB 数据一次性回填进去，留个
复合索引给 ``(goal_id, round_number, status)`` 查询——EXECUTING 阶段轮询会用到。

不动老 payload 字段，留作向下兼容（第三方读写 JSONB 的代码不破）。
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "011_agent_runs_round_number"
down_revision = "010_goal_fk_cascade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 加列（nullable，让老 run 不被卡住）
    op.execute(
        "ALTER TABLE agent_runs "
        "ADD COLUMN IF NOT EXISTS round_number INTEGER;"
    )

    # 2) 从 JSONB payload 回填历史值
    #    payload ? 'round_number' 过滤掉"压根没碰过 round_number"的早期 run
    op.execute(
        "UPDATE agent_runs "
        "SET round_number = NULLIF(payload->>'round_number', '')::int "
        "WHERE round_number IS NULL "
        "  AND payload ? 'round_number';"
    )

    # 3) 复合索引：goal_id + round_number + status（orchestrator EXECUTING 轮询的命中 pattern）
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_goal_round_status "
        "ON agent_runs(goal_id, round_number, status);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_goal_round_status;")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS round_number;")
    # JSONB payload 是只读的，不在这里还原
