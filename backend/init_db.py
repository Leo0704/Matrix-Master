"""用 SQLAlchemy metadata.create_all 建表（替代 001_initial.py 的 alembic 迁移）。

原因：001_initial.py 用 op.execute 一大段 SQL 跑全 schema，但 asyncpg prepared
statement 不支持多语句，导致迁移失败。

这个脚本只跑一次：建完所有表后用 `alembic stamp head` 标记 001 已应用。
后续 002/003/004 走 alembic 正常路径。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def main() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine
    from matrix.db.models import Base

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL env var required")
        sys.exit(1)
    print(f"Connecting to {url}")
    engine = create_async_engine(url, echo=False)

    async with engine.begin() as conn:
        # 启用 pgvector extension
        await conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
        await conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS pgcrypto;')
        await conn.exec_driver_sql('CREATE EXTENSION IF NOT EXISTS vector;')
        # 建表
        await conn.run_sync(Base.metadata.create_all)
        print("✓ All tables created")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
