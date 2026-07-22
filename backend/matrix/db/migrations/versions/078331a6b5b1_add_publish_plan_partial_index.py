"""add publish plan partial index

Revision ID: 078331a6b5b1
Revises: 018_business_id_immutable_and_alerts_business
Create Date: 2026-07-20 08:15:19.346714

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '078331a6b5b1'
down_revision: Union[str, None] = '018_business_id_immutable_and_alerts_business'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 每个 goal 最多一个 publish plan，避免每条笔记一条冗余 plan 行。
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_plans_publish
            ON plans(goal_id)
            WHERE steps ->> 'kind' = 'publish';
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_plans_publish;")