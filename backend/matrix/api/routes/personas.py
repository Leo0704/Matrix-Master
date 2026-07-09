"""人设 CRUD 端点。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from matrix.api.deps import get_db
from matrix.api.schemas import (
    Persona,
    PersonaCreate,
    PersonaListResponse,
    PersonaUpdate,
)
from matrix.db.models import Persona as PersonaORM
from matrix.monitoring.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/personas", tags=["personas"])


def _to_schema(p: PersonaORM) -> Persona:
    return Persona(
        id=p.id,
        name=p.name,
        tone=p.tone,
        style_guide=p.style_guide,
        forbidden_words=list(p.forbidden_words or []),
        sample_note_ids=[uuid.UUID(str(x)) for x in (p.sample_note_ids or [])],
        version=p.version,
    )


@router.get("", response_model=PersonaListResponse)
async def list_personas(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db),
) -> PersonaListResponse:
    stmt = (
        select(PersonaORM)
        .where(PersonaORM.deleted_at.is_(None))
        .order_by(PersonaORM.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return PersonaListResponse(items=[_to_schema(r) for r in rows])


@router.get("/{persona_id}", response_model=Persona)
async def get_persona(
    persona_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> Persona:
    p = await session.get(PersonaORM, persona_id)
    if p is None or p.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")
    return _to_schema(p)


@router.post("", response_model=Persona, status_code=status.HTTP_201_CREATED)
async def create_persona(
    body: PersonaCreate,
    session: AsyncSession = Depends(get_db),
) -> Persona:
    exists = (
        await session.execute(select(PersonaORM).where(PersonaORM.name == body.name))
    ).scalar_one_or_none()
    if exists is not None and exists.deleted_at is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"persona name '{body.name}' already exists",
        )

    p = PersonaORM(
        name=body.name,
        tone=body.tone,
        style_guide=body.style_guide,
        forbidden_words=body.forbidden_words,
        sample_note_ids=body.sample_note_ids,
        version=1,
    )
    session.add(p)
    await session.flush()
    return _to_schema(p)


@router.patch("/{persona_id}", response_model=Persona)
async def update_persona(
    persona_id: uuid.UUID,
    body: PersonaUpdate,
    session: AsyncSession = Depends(get_db),
) -> Persona:
    p = await session.get(PersonaORM, persona_id)
    if p is None or p.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")

    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != p.name:
        # name 唯一性预检
        exists = (
            await session.execute(
                select(PersonaORM).where(PersonaORM.name == data["name"])
            )
        ).scalar_one_or_none()
        if exists is not None and exists.id != persona_id and exists.deleted_at is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"persona name '{data['name']}' already exists",
            )
        p.name = data["name"]
    if "tone" in data:
        p.tone = data["tone"]
    if "style_guide" in data:
        p.style_guide = data["style_guide"]
    if "forbidden_words" in data:
        p.forbidden_words = data["forbidden_words"]
    if "sample_note_ids" in data:
        p.sample_note_ids = data["sample_note_ids"]
    p.version = (p.version or 0) + 1
    p.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return _to_schema(p)


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_persona(
    persona_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> None:
    p = await session.get(PersonaORM, persona_id)
    if p is None or p.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "persona not found")
    p.deleted_at = datetime.now(timezone.utc)
    p.updated_at = p.deleted_at
    await session.flush()
    logger.info("persona.soft_delete", persona_id=persona_id)
