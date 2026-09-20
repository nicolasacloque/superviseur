"""Consultation du journal d'audit (administrateur)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import SessionDep, require_role
from app.api.schemas import AuditOut
from app.common.models import RoleName
from app.db.models import AuditLog

router = APIRouter(tags=["audit"], dependencies=[Depends(require_role(RoleName.ADMIN))])


@router.get("/audit", response_model=list[AuditOut])
async def list_audit(
    session: SessionDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    user: uuid.UUID | None = None,
    action: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[AuditLog]:
    query = select(AuditLog).order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(limit)
    if from_ is not None:
        query = query.where(AuditLog.ts >= from_)
    if to is not None:
        query = query.where(AuditLog.ts < to)
    if user is not None:
        query = query.where(AuditLog.user_id == user)
    if action:
        query = query.where(AuditLog.action == action)
    return list((await session.scalars(query)).all())
