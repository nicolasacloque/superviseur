"""Écriture d'une consigne : API -> Redis (stream cmd.write) -> collecteur -> contrôleur."""

from __future__ import annotations

import time
import uuid
from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request, status

from app.api.audit import record_audit
from app.api.commands import send_and_wait
from app.api.deps import RedisDep, SessionDep, SettingsDep, UserDep, checked_in_handler
from app.api.schemas import WriteRequest, WriteResponse
from app.auth.permissions import has_role
from app.common.bus import STREAM_WRITE, write_result_channel
from app.common.models import RoleName
from app.db.models import Point

UNPROCESSABLE = 422  # nom du code HTTP variable selon les versions de Starlette

router = APIRouter(tags=["write"])


@router.post("/points/{point_id}/write", response_model=WriteResponse)
@checked_in_handler(RoleName.OPERATOR)
async def write_point(
    point_id: uuid.UUID,
    body: WriteRequest,
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    user: UserDep,
) -> WriteResponse:
    """Écrit `present-value` avec une priorité BACnet (défaut 8) ; `value: null` relâche."""
    attempt = {"value": body.value, "priority": body.priority}

    async def refuse(code: int, detail: str, label: str | None = None) -> NoReturn:
        record_audit(
            session,
            request,
            user_id=user.id,
            action="point.write",
            target=label or str(point_id),
            after={**attempt, "result": f"refusé: {detail}"},
        )
        await session.commit()
        raise HTTPException(code, detail)

    if not has_role(user.role, RoleName.OPERATOR):
        await refuse(status.HTTP_403_FORBIDDEN, "droits insuffisants")
    point = await session.get(Point, point_id)
    if point is None:
        await refuse(status.HTTP_404_NOT_FOUND, "point introuvable")
    label = point.path or str(point_id)
    if not point.writable:
        await refuse(status.HTTP_409_CONFLICT, "point non inscriptible", label)
    if body.value is not None:
        if point.write_min is not None and body.value < point.write_min:
            await refuse(
                UNPROCESSABLE,
                f"valeur inférieure au minimum ({point.write_min})",
                label,
            )
        if point.write_max is not None and body.value > point.write_max:
            await refuse(
                UNPROCESSABLE,
                f"valeur supérieure au maximum ({point.write_max})",
                label,
            )

    command_id = uuid.uuid4().hex
    command = {
        "command_id": command_id,
        "point_id": str(point_id),
        "value": body.value,
        "priority": body.priority,
        "user_id": str(user.id),
        "issued_at": time.time(),  # le collecteur ignore les commandes trop anciennes
    }
    result = await send_and_wait(
        redis,
        STREAM_WRITE,
        write_result_channel(command_id),
        command,
        settings.write_timeout_s,
    )
    if result is None:
        # Le collecteur ne répond pas : la commande périmera d'elle-même côté collecteur.
        await refuse(status.HTTP_504_GATEWAY_TIMEOUT, "le collecteur n'a pas répondu", label)
    if result.get("status") != "ok":
        # Le collecteur a tracé lui-même cette tentative dans le journal d'audit.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, result.get("message") or "écriture refusée"
        )
    return WriteResponse(status="ok", command_id=command_id)
