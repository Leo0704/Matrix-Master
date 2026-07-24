"""账号 CRUD 端点。"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db, resolve_active_business
from matrix.api.schemas import Account, AccountCreate, AccountListResponse, AccountUpdate
from matrix.db.models import Account as AccountORM, Device, KbDocument

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _to_schema(a: AccountORM) -> Account:
    return Account(
        id=a.id,
        handle=a.handle,
        persona_id=a.persona_id,
        device_id=a.device_id,
        business_id=a.business_id,  # v0.7+ 业务归属
        status=a.status,  # type: ignore[arg-type]
        last_active=a.last_active,
        risk_score=float(a.risk_score or 0),
    )


@router.get("", response_model=AccountListResponse)
async def list_accounts(
    device_id: Optional[uuid.UUID] = Query(None),
    persona_id: Optional[uuid.UUID] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    business_id: Optional[uuid.UUID] = Query(None, description="v0.7+ 业务过滤"),
    session: AsyncSession = Depends(get_db),
) -> AccountListResponse:
    stmt = select(AccountORM).where(AccountORM.deleted_at.is_(None))
    if device_id:
        stmt = stmt.where(AccountORM.device_id == device_id)
    if persona_id:
        stmt = stmt.where(AccountORM.persona_id == persona_id)
    if status_filter:
        stmt = stmt.where(AccountORM.status == status_filter)
    if business_id:
        stmt = stmt.where(AccountORM.business_id == business_id)
    stmt = stmt.order_by(AccountORM.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return AccountListResponse(items=[_to_schema(r) for r in rows])


@router.post("", response_model=Account, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: AccountCreate,
    session: AsyncSession = Depends(get_db),
) -> Account:
    # v0.7+ 业务模型重构：业务上下文校验（存在 + active）
    await resolve_active_business(session, body.business_id)

    # 外键存在性校验（FK 不会立刻抛错，先查后插更友好）
    device = await session.get(Device, body.device_id)
    if device is None or device.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "device not found")
    if body.persona_id is not None:
        persona_doc = await session.get(KbDocument, body.persona_id)
        if persona_doc is None or persona_doc.type != "persona":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "persona document not found")

    # 一机一账号预检（migration 007 加的 partial unique index 兜底，这里给友好错误码）
    conflict = (
        await session.execute(
            select(AccountORM).where(
                AccountORM.device_id == body.device_id,
                AccountORM.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if conflict is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"device {body.device_id} already bound to account '{conflict.handle}'",
        )

    # handle 唯一性预检
    exists = (
        await session.execute(
            select(AccountORM).where(AccountORM.handle == body.handle)
        )
    ).scalar_one_or_none()
    if exists is not None and exists.deleted_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"account handle '{body.handle}' already exists",
        )

    a = AccountORM(
        handle=body.handle,
        device_id=body.device_id,
        persona_id=body.persona_id,
        business_id=body.business_id,  # v0.7+ 业务归属
        status="pending",
        risk_score=0,
    )
    session.add(a)
    await session.flush()
    return _to_schema(a)


def _validate_status_transition(from_status: str, to_status: str) -> None:
    """校验账号状态转移是否允许，非法时抛 409 CONFLICT。

    允许规则：
    - pending → active
    - active → suspended
    - suspended → active
    - 任意非 banned 状态 → disabled
    - 同状态视为幂等，直接允许
    - banned 状态（进入或离开）不允许 API 直改
    """
    if from_status == to_status:
        return
    if from_status == "banned" or to_status == "banned":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "banned status cannot be changed via API",
        )
    allowed = {
        ("pending", "active"),
        ("active", "suspended"),
        ("suspended", "active"),
        ("pending", "disabled"),
        ("active", "disabled"),
        ("suspended", "disabled"),
    }
    if (from_status, to_status) not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"invalid status transition from '{from_status}' to '{to_status}'",
        )


@router.get("/{account_id}", response_model=Account)
async def get_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
) -> Account:
    a = await session.get(AccountORM, account_id)
    if a is None or a.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    return _to_schema(a)


@router.patch("/{account_id}", response_model=Account)
async def update_account(
    account_id: uuid.UUID,
    body: AccountUpdate,
    session: AsyncSession = Depends(get_db),
) -> Account:
    """改账号属性（局部更新）。

    换绑 device_id 时受 1:1 唯一约束保护（DB 抛 IntegrityError → 转 409）。
    设备退役（同时解绑账号）请走独立 ``POST /devices/{id}/retire`` 端点（语义清晰）。
    """
    a = await session.get(AccountORM, account_id)
    if a is None or a.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "account not found")
    if body.handle is not None and body.handle != a.handle:
        # handle 唯一性预检
        exists = (
            await session.execute(
                select(AccountORM).where(AccountORM.handle == body.handle)
            )
        ).scalar_one_or_none()
        if exists is not None and exists.deleted_at is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"account handle '{body.handle}' already exists",
            )
        a.handle = body.handle
    if body.persona_id is not None:
        persona_doc = await session.get(KbDocument, body.persona_id)
        if persona_doc is None or persona_doc.type != "persona":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "persona document not found")
        # W5 业务隔离：persona 绑了别的业务就拒绝（NULL = 全局共享，允许）
        if (
            persona_doc.business_id is not None
            and persona_doc.business_id != a.business_id
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "persona document belongs to another business",
            )
        a.persona_id = body.persona_id
    if body.device_id is not None and body.device_id != a.device_id:
        # 一机一账号预检（migration 007 partial unique index 兜底）
        device = await session.get(Device, body.device_id)
        if device is None or device.deleted_at is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "device not found")
        a.device_id = body.device_id
    if body.status is not None and body.status != a.status:
        _validate_status_transition(a.status, body.status)
        a.status = body.status
    await session.flush()
    return _to_schema(a)
