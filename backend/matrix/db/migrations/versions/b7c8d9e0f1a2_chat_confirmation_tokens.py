"""chat_confirmation_tokens 表：聊天确认令牌落 DB（替代进程内存 dict）

``matrix.api.routes.chat`` 的 preview_change 两阶段确认令牌原存进程内
``_CONFIRMATION_STORE``，多 worker / 多实例部署时 /confirm 可能打到另一个
进程而丢令牌。落 DB 后任意实例都能消费；/confirm、/cancel 消费即删，
过期行由 consume 时按 expires_at 判定，无需后台清理。

Revises: 32fc4695eee8
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "b7c8d9e0f1a2"
down_revision = "32fc4695eee8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_confirmation_tokens (
            token       VARCHAR(64) PRIMARY KEY,
            args        JSONB NOT NULL DEFAULT '{}'::jsonb,
            business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            expires_at  TIMESTAMPTZ NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_chat_confirmation_tokens_expiry
            ON chat_confirmation_tokens(expires_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_chat_confirmation_tokens_expiry;")
    op.execute("DROP TABLE IF EXISTS chat_confirmation_tokens;")
