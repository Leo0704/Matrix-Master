"""enforce 一机一账号：accounts.device_id 加 partial unique index

业务约束（老板明确）：一台设备只固定一个账号，除非设备坏了换新。
代码层加 partial unique index（仅约束 deleted_at IS NULL），软删的旧账号
不影响新账号绑定同一设备。

Revision ID: 007_unique_device_per_account
Revises: 006_notes_optional_account
Create Date: 2026-07-11
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "007_unique_device_per_account"
down_revision = "006_notes_optional_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_device_active "
        "ON accounts(device_id) WHERE deleted_at IS NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_accounts_device_active;")