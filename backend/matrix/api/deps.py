"""FastAPI 依赖：DB session、鉴权等。

设计原则：
- ``get_db`` 直接复用 ``matrix.db.session.get_session``（它已是 async context manager），
  拿不到就抛 500 而不是 200。session 生命周期由依赖函数管。
- ``get_current_user`` 是 stub：Tauri 本地调用通过共享 secret 鉴权，目前放行所有本地请求；
  后续可以加上 token 校验。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.db.session import get_session_factory


@dataclass
class CurrentUser:
    """当前调用者。本地部署只有一个 OS 用户。"""

    name: str = "operator"


# ---------------------------------------------------------------------------
# DB session
# ---------------------------------------------------------------------------


async def get_db() -> AsyncIterator[AsyncSession]:
    """注入一个 async DB session，结束时自动 commit / rollback / close。

    用法::

        @router.get("/foo")
        async def foo(session: AsyncSession = Depends(get_db)):
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


# ---------------------------------------------------------------------------
# Auth（运营者 OS 用户）
# ---------------------------------------------------------------------------


async def get_current_user(request: Request) -> CurrentUser:
    """运营者鉴权（stub）。

    Tauri shell 通过 Unix socket / localhost + 共享 secret 鉴权；本端只做存在性校验：
    - ``Authorization`` header 非空 + 匹配 ``MATRIX_API_SECRET`` 环境变量（若设置），否则放行
    - 生产部署应在反向代理层做 IP / socket 隔离
    """
    expected = os.environ.get("MATRIX_API_SECRET")
    if expected:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ").strip()
        if token != expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid api secret",
            )
    return CurrentUser()
