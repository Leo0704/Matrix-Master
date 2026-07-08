"""add product type to kb_documents

主题贯穿改造 §4: KB 加 type=product 作为商品事实库。
DRAFT 节点按 product type 检索商品事实（款式/尺码/价格/卖点）。

Revision ID: 003_kb_product_type
Revises: 002_add_kb_review
Create Date: 2026-07-09 12:00:00
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "003_kb_product_type"
down_revision = "002_add_kb_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 扩展 CHECK 约束：加 'product' 类型
    op.execute("ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_type_check;")
    op.execute(
        """
        ALTER TABLE kb_documents
            ADD CONSTRAINT kb_documents_type_check
            CHECK (type IN ('brand', 'persona', 'rule', 'topic', 'history', 'template', 'product'));
        """
    )

    # 2) 商品库索引：按 type='product' 加速列表/检索
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_kb_documents_product
            ON kb_documents(type) WHERE type = 'product' AND deleted_at IS NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_kb_documents_product;")
    op.execute("ALTER TABLE kb_documents DROP CONSTRAINT IF EXISTS kb_documents_type_check;")
    op.execute(
        """
        ALTER TABLE kb_documents
            ADD CONSTRAINT kb_documents_type_check
            CHECK (type IN ('brand', 'persona', 'rule', 'topic', 'history', 'template'));
        """
    )
