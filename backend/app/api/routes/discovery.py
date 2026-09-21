"""Relance manuelle de la découverte BACnet."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.audit import record_audit
from app.api.deps import CurrentUser, RedisDep, SessionDep, require_role
from app.common.bus import CHANNEL_DISCOVERY_RUN
from app.common.models import RoleName

router = APIRouter(tags=["discovery"])
Engineer = Annotated[CurrentUser, Depends(require_role(RoleName.ENGINEER))]


class DiscoveryRequested(BaseModel):
    requested: bool = True


@router.post(
    "/discovery/run", response_model=DiscoveryRequested, status_code=status.HTTP_202_ACCEPTED
)
async def run_discovery(
    request: Request, session: SessionDep, redis: RedisDep, user: Engineer
) -> DiscoveryRequested:
    """Demande au collecteur de relancer la découverte ; le résultat apparaît dans `/devices`."""
    receivers = await redis.publish(CHANNEL_DISCOVERY_RUN, "run")
    if not receivers:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "le collecteur ne répond pas")
    record_audit(session, request, user_id=user.id, action="discovery.run", target=None)
    await session.commit()
    return DiscoveryRequested()
