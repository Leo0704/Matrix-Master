"""add image_asset to kb_documents_type_check

Revision ID: 9a1b2c3d4e5f
Revises: 078331a6b5b1
Create Date: 2026-07-22 04:55:00.000000

E2E 实测发现：IMAGE_GEN 节点把生成的图片 URL 缓存进 KB 时
（doc_type=image_asset），一是 upsert 参数名对不上（doc_type vs type），
二是 DB CHECK 约束没有 image_asset 这个类型。本迁移放开约束。
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9a1b2c3d4e5f'
down_revision: Union[str, None] = '078331a6b5b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_type_check"
    )
    op.execute(
        "ALTER TABLE kb_documents ADD CONSTRAINT kb_documents_type_check "
        "CHECK (type IN ('brand', 'persona', 'rule', 'topic', 'history', "
        "'template', 'product', 'strategy_card', 'image_asset'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_type_check"
    )
    op.execute(
        "ALTER TABLE kb_documents ADD CONSTRAINT kb_documents_type_check "
        "CHECK (type IN ('brand', 'persona', 'rule', 'topic', 'history', "
        "'template', 'product', 'strategy_card'))"
    )
