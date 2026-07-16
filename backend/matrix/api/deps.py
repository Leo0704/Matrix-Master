"""FastAPI 依赖：DB session、鉴权等。

设计原则：
- ``get_db`` 直接复用 ``matrix.db.session.get_session``（它已是 async context manager），
  拿不到就抛 500 而不是 200。session 生命周期由依赖函数管。
- ``get_current_user`` 是 stub：本地/前端调用通过共享 secret 鉴权，目前放行所有本地请求；
  后续可以加上 token 校验。
- ``resolve_active_business``（v0.7+）：POST 路由统一校验业务上下文（存在 + active）。
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import HTTPException, Request, status
from sqlalchemy import and_, exists, or_
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

    Web frontend / 本机调用通过 localhost + 共享 secret 鉴权；本端只做存在性校验：
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


# ---------------------------------------------------------------------------
# 业务校验（v0.7+ 业务模型重构）
# ---------------------------------------------------------------------------


async def resolve_active_business(
    session: AsyncSession, business_id: uuid.UUID
):
    """校验业务存在 + status='active'，返回 BusinessORM。

    - 不存在 → 404 NOT_FOUND
    - archived → 409 CONFLICT（archived 业务下不能再创建资源）
    """
    # 局部 import 避免循环（deps → models → ... → deps 的潜在回路）
    from matrix.db.models import Business as BusinessORM

    biz = await session.get(BusinessORM, business_id)
    if biz is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="business not found"
        )
    if biz.status == "archived":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot create under archived business",
        )
    return biz


def filter_derived_by_business(
    stmt,
    *,
    business_id: uuid.UUID | None,
    sources: list[tuple],
):
    """衍生表 list 业务过滤 helper（v0.7+）。

    衍生表（interactions/agent_runs/alerts/notifications 等）自身没有 business_id 列，
    需要通过父表（Account/Goal/Device 等）的 business_id 间接过滤。

    用法::

        sources = [
            (InteractionORM, Account, "account_id"),  # (child, parent, fk_attr_name)
            (InteractionORM, Note,    "target_note_id"),
        ]
        stmt = filter_derived_by_business(stmt, business_id=bid, sources=sources)

    business_id 为 None 时不添加过滤。
    """
    if business_id is None:
        return stmt

    or_conds = []
    for child, parent, fk_name in sources:
        fk_attr = getattr(child, fk_name)
        or_conds.append(
            and_(
                fk_attr.is_not(None),
                exists().where(
                    parent.id == fk_attr,
                    parent.business_id == business_id,
                ),
            )
        )
    if not or_conds:
        return stmt
    return stmt.where(or_(*or_conds))
