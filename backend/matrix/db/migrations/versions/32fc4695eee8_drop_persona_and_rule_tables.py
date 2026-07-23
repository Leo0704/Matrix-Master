"""drop_persona_and_rule_tables

Revision ID: 32fc4695eee8
Revises: 9a1b2c3d4e5f
Create Date: 2026-07-23 15:15:06.769928

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '32fc4695eee8'
down_revision: Union[str, None] = '9a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) 先删 accounts 指向 personas 的外键
    op.drop_constraint('accounts_persona_id_fkey', 'accounts', type_='foreignkey')
    # 2) 删 personas 表
    op.drop_table('personas')
    # 3) 删 rules 表
    op.drop_table('rules')


def downgrade() -> None:
    # 恢复 rules 表
    op.create_table(
        'rules',
        sa.Column('id', sa.UUID(), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('category', sa.VARCHAR(length=32), nullable=False),
        sa.Column('text', sa.TEXT(), nullable=False),
        sa.Column('severity', sa.SMALLINT(), nullable=False),
        sa.Column('source', sa.VARCHAR(length=64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('severity >= 1 AND severity <= 5', name='rules_severity_check'),
        sa.PrimaryKeyConstraint('id', name='rules_pkey'),
    )
    op.create_index('idx_rules_category', 'rules', ['category'], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))

    # 恢复 personas 表
    op.create_table(
        'personas',
        sa.Column('id', sa.UUID(), server_default=sa.text('uuid_generate_v4()'), nullable=False),
        sa.Column('name', sa.VARCHAR(length=64), nullable=False),
        sa.Column('tone', sa.VARCHAR(length=256), nullable=False),
        sa.Column('style_guide', sa.TEXT(), nullable=False),
        sa.Column('forbidden_words', sa.ARRAY(sa.TEXT()), server_default=sa.text("'{}'::text[]"), nullable=False),
        sa.Column('sample_note_ids', sa.ARRAY(sa.UUID()), server_default=sa.text("'{}'::uuid[]"), nullable=False),
        sa.Column('version', sa.INTEGER(), server_default=sa.text('1'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('business_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], name='personas_business_id_fkey', ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id', name='personas_pkey'),
        sa.UniqueConstraint('business_id', 'name', name='personas_business_id_name_key'),
    )

    # 恢复 accounts 外键
    op.create_foreign_key('accounts_persona_id_fkey', 'accounts', 'personas', ['persona_id'], ['id'])
