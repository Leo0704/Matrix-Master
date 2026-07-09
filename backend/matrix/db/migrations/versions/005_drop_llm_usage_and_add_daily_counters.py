"""drop llm_usage & add daily_counters

LLM 计费功能下线：
- 删除 ``llm_usage`` 表与其索引
- 删除 app_config 中的 ``llm.daily_budget_usd`` 键
- 删除 monitoring 中的 BUDGET_EXCEEDED 告警类型

RateLimiter 改走 DB 原子计数：
- 新增 ``daily_counters`` 表（按 (scope, key, kind, day) 唯一，自增 count）
- 替换原进程内 ``_DailyCounter``，uvicorn workers>1 不再绕过日上限

Revision ID: 005_drop_llm_usage_and_add_daily_counters
Revises: 004_alerts_table
Create Date: 2026-07-10 12:00:00
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "005_drop_llm_usage_and_add_daily_counters"
down_revision = "004_alerts_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0. 扩 alembic_version.version_num 列宽
    # 默认 VARCHAR(32) 装不下本 revision id（41 字符）；放在所有 op.execute 同 transaction 里
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64);")

    # 1. 删 llm_usage 相关
    op.execute("DROP TABLE IF EXISTS llm_usage CASCADE;")
    op.execute(
        "DELETE FROM app_config WHERE key = 'llm.daily_budget_usd';"
    )

    # 2. 新增 daily_counters（限速日上限原子计数）
    op.execute(
        """
        CREATE TABLE daily_counters (
            scope       VARCHAR(32)  NOT NULL,
            key         VARCHAR(64)  NOT NULL,
            kind        VARCHAR(32)  NOT NULL,
            day         DATE         NOT NULL,
            count       INTEGER      NOT NULL DEFAULT 0,
            updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            PRIMARY KEY (scope, key, kind, day)
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_daily_counters_day "
        "ON daily_counters(day DESC);"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_counters CASCADE;")
    # llm_usage 不重建（已下线，避免回潮）
