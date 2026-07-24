"""notifications 加 business_id 列（W5：通知业务归属两本账合一）

1) notifications 表加 nullable business_id UUID 列 + FK（ON DELETE SET NULL，
   与本表其他 4 个 typed FK 一致——通知是日志，业务删除不该被挡）
2) 加普通索引（list/read/clear-read 按业务过滤走它）

老数据 business_id 为 NULL：查询层回退到 goal/run/note/device FK EXISTS 推导，
本迁移不做回填（FK 推导已覆盖，且 payload 里的 business_id 字符串不一定是合法 UUID）。

Revision ID: 019_notifications_business_id
Revises: b7c8d9e0f1a2
Create Date: 2026-07-23

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "019_notifications_business_id"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS business_id UUID")
    op.execute(
        "ALTER TABLE notifications "
        "DROP CONSTRAINT IF EXISTS notifications_business_id_fkey"
    )
    op.execute(
        "ALTER TABLE notifications "
        "ADD CONSTRAINT notifications_business_id_fkey "
        "FOREIGN KEY (business_id) REFERENCES businesses(id) "
        "ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_business_id "
        "ON notifications(business_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_notifications_business_id")
    op.execute(
        "ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_business_id_fkey"
    )
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS business_id")
