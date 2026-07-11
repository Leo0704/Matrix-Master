"""让 notes.account_id 可空：草稿阶段先落库，账号等 DISPATCH 时再绑

背景：当前 agent 流程里 DRAFT 节点生成的草稿从不落 notes 表，
只有前端手工 POST /api/notes 才能写一条。要让草稿在 DRAFT 阶段就
落库（DISPATCH 失败/无设备时老板也能在「草稿」页看到内容），但
DRAFT 阶段还不知道用哪个账号（SCHEDULE 之后才有），所以让
account_id 暂可为 NULL；DISPATCH 成功后由 publish_node 绑定。

Revision ID: 006_notes_optional_account
Revises: 005_drop_llm_usage_and_add_daily_counters
Create Date: 2026-07-11
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "006_notes_optional_account"
down_revision = "005_drop_llm_usage_and_add_daily_counters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notes ALTER COLUMN account_id DROP NOT NULL;")


def downgrade() -> None:
    # 回滚：把 NULL 的 account_id 填一个占位（理论上不应该有遗留 NULL，
    # 因为现状是没人写 notes；保险起见用 NOT VALID FK 防失败）
    op.execute(
        "UPDATE notes SET account_id = (SELECT id FROM accounts LIMIT 1) "
        "WHERE account_id IS NULL;"
    )
    op.execute("ALTER TABLE notes ALTER COLUMN account_id SET NOT NULL;")