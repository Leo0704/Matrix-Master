"""Phase 1: notifications 表 + notes 调度采集列 + plans 复用索引

P1-1（反向反馈通道 + 24h 延时回采）的 DB 基础：

1. ``notifications`` 表 —— 终态用户/运营通知。
   不同于 ``alerts``（critical/warning 监控语义），本表覆盖进度/结果，
   severity 含 success/info/warning/error 四档，read_at 表示"已读"而非"已解决"。
   模仿 ``004_alerts_table.py`` 风格。

2. ``notes`` 加 3 列：
   - ``scheduled_collect_at``：发布成功后由 publish_node 写入 = now + 24h
   - ``collected_at``：collect 执行成功后由 ``_do_collect`` 写入
   - ``collected_run_id``：collect 时所属 run（用于 KPI 追溯）
   模仿 ``012_notes_goal_run_id.py`` 风格，partial index 只覆盖未采的发布笔记。

3. ``plans`` 加 partial unique index：
   publish_node 排 24h 采集时复用同一 ``plans`` 行（steps.kind='post_publish_collect'），
   避免每条笔记一条 plan 行。partial unique 保证每个 goal 最多一个 collect plan。

Revises: d0a5fb51f30f（strategy_card 必须先落，否则 phase1 迁移会分叉 head）
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "f1e2d3c4b5a6"
down_revision = "d0a5fb51f30f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- notifications 表 ------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            recipient   VARCHAR(64) NOT NULL,
            code        VARCHAR(64) NOT NULL,
            severity    VARCHAR(16) NOT NULL
                            CHECK (severity IN ('info','success','warning','error')),
            title       VARCHAR(256) NOT NULL,
            body        TEXT NOT NULL,
            goal_id     UUID REFERENCES goals(id) ON DELETE SET NULL,
            run_id      UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
            note_id     UUID REFERENCES notes(id) ON DELETE SET NULL,
            device_id   UUID REFERENCES devices(id) ON DELETE SET NULL,
            payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
            read_at     TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notifications_unread
            ON notifications(recipient, created_at DESC) WHERE read_at IS NULL;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notifications_goal
            ON notifications(goal_id, created_at DESC) WHERE goal_id IS NOT NULL;
        """
    )

    # ---- notes 三列 -------------------------------------------------------
    op.execute(
        "ALTER TABLE notes "
        "ADD COLUMN IF NOT EXISTS scheduled_collect_at TIMESTAMPTZ;"
    )
    op.execute(
        "ALTER TABLE notes "
        "ADD COLUMN IF NOT EXISTS collected_at TIMESTAMPTZ;"
    )
    op.execute(
        "ALTER TABLE notes "
        "ADD COLUMN IF NOT EXISTS collected_run_id UUID "
        "REFERENCES agent_runs(id) ON DELETE SET NULL;"
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_notes_scheduled_collect_at
            ON notes(scheduled_collect_at)
            WHERE status = 'published' AND collected_at IS NULL;
        """
    )

    # ---- plans 复用约束 ----------------------------------------------------
    # publish_node 排 24h 采集时复用同一 plans 行；partial unique 约束
    # 每个 goal 最多一个 post_publish_collect plan，避免每条笔记冗余一条。
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_plans_post_publish_collect
            ON plans(goal_id)
            WHERE steps ->> 'kind' = 'post_publish_collect';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_plans_post_publish_collect;")
    op.execute("DROP INDEX IF EXISTS idx_notes_scheduled_collect_at;")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS collected_run_id;")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS collected_at;")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS scheduled_collect_at;")
    op.execute("DROP INDEX IF EXISTS idx_notifications_goal;")
    op.execute("DROP INDEX IF EXISTS idx_notifications_unread;")
    op.execute("DROP TABLE IF EXISTS notifications;")