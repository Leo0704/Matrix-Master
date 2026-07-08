"""Async SQLAlchemy engine 工厂。

连接 PostgreSQL（asyncpg 驱动），从环境变量读取 DATABASE_URL。
"""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def get_database_url(url: Optional[str] = None) -> str:
    """从参数或环境变量读 DATABASE_URL。

    默认值仅供本地开发，生产环境必须显式设置。
    """
    return url or os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://matrix:matrix@localhost:5432/matrix",
    )


def create_engine(
    url: Optional[str] = None,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_pre_ping: bool = True,
    pool_recycle: int = 1800,
    echo: bool = False,
    **kwargs,
) -> AsyncEngine:
    """创建 async SQLAlchemy engine。

    Args:
        url: 数据库 URL；缺省从 DATABASE_URL 环境变量读
        pool_size: 池大小（仅 PostgreSQL / MySQL 等）
        max_overflow: 超出 pool_size 后允许的临时连接数（仅 PG / MySQL）
        pool_pre_ping: 每次取出连接前探活（防止 stale 连接）
        pool_recycle: 连接复用上限（秒）
        echo: 是否打印 SQL（调试用）
    """
    db_url = get_database_url(url)
    # SQLite（aiosqlite）用 StaticPool，不接受 pool_size / max_overflow
    is_sqlite = db_url.startswith("sqlite")
    engine_kwargs: dict = {"echo": echo, **kwargs}
    if not is_sqlite:
        engine_kwargs.update(
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            pool_recycle=pool_recycle,
        )
    return create_async_engine(db_url, **engine_kwargs)