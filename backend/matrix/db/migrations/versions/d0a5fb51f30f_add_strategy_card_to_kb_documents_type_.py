"""add strategy_card to kb_documents_type_check

Revision ID: d0a5fb51f30f
Revises: 014_device_identity_nullable
Create Date: 2026-07-13 08:03:01.599795

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd0a5fb51f30f'
down_revision: Union[str, None] = '014_device_identity_nullable'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_type_check"
    )
    op.execute(
        "ALTER TABLE kb_documents ADD CONSTRAINT kb_documents_type_check "
        "CHECK (type IN ('brand', 'persona', 'rule', 'topic', 'history', "
        "'template', 'product', 'strategy_card'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_type_check"
    )
    op.execute(
        "ALTER TABLE kb_documents ADD CONSTRAINT kb_documents_type_check "
        "CHECK (type IN ('brand', 'persona', 'rule', 'topic', 'history', "
        "'template', 'product'))"
    )
