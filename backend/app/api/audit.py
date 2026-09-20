"""Journal d'audit côté API (section 11)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def record_audit(
    session: AsyncSession,
    request: Request,
    *,
    user_id: uuid.UUID | None,
    action: str,
    target: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    """Ajoute une ligne d'audit à la session ; l'appelant valide la transaction."""
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target=target,
            before=before,
            after=after,
            ip=client_ip(request),
        )
    )
