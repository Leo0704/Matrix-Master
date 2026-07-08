"""Async session 上下文管理器 / FastAPI 依赖。

提供：
- `SessionLocal`：sessionmaker 工厂
- `get_session()`：async context manager（手动调用）
- `get_session()`：可作为 FastAPI Depends 注入
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from matrix.db.engine import create_engine


# 模块级 engine / sessionmaker：首次访问时惰性创建
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """获取（惰性创建）模块级 AsyncEngine。"""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def set_engine(engine: Optional[AsyncEngine]) -> None:
    """覆盖模块级 engine。供测试或配置切换使用。"""
    global _engine, _session_factory
    _engine = engine
    _session_factory = None  # 失效，下次重建


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取（惰性创建）模块级 sessionmaker。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """async session 上下文管理器。

    用法：
        async with get_session() as session:
            ...

    FastAPI 依赖：
        async def endpoint(session: AsyncSession = Depends(get_session)):
            ...
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()