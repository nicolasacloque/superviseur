"""Écriture d'une consigne : API -> Redis (stream cmd.write) -> collecteur -> contrôleur."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request, status

from app.api.audit import record_audit
from app.api.deps import RedisDep, SessionDep, SettingsDep, UserDep
from app.api.schemas import WriteRequest, WriteResponse
from app.auth.permissions import has_role
from app.common.bus import STREAM_WRITE, write_result_channel
from app.common.models import RoleName
from app.db.models import Point

UNPROCESSABLE = 422  # nom du code HTTP variable selon les versions de Starlette

router = APIRouter(tags=["write"])

STREAM_MAX_LEN = 10_000


@router.post("/points/{point_id}/write", response_model=WriteResponse)
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
    result = await _send_and_wait(redis, command, settings.write_timeout_s)
    if result is None:
        # Le collecteur ne répond pas : la commande périmera d'elle-même côté collecteur.
        await refuse(status.HTTP_504_GATEWAY_TIMEOUT, "le collecteur n'a pas répondu", label)
    if result.get("status") != "ok":
        # Le collecteur a tracé lui-même cette tentative dans le journal d'audit.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, result.get("message") or "écriture refusée"
        )
    return WriteResponse(status="ok", command_id=command_id)


async def _send_and_wait(
    redis: Any, command: dict[str, Any], timeout_s: float
) -> dict[str, Any] | None:
    """Publie la commande puis attend son résultat ; `None` si le délai est dépassé."""
    pubsub = redis.pubsub()
    # Abonnement avant l'envoi : le résultat ne peut pas arriver avant qu'on l'écoute.
    await pubsub.subscribe(write_result_channel(command["command_id"]))
    try:
        await redis.xadd(
            STREAM_WRITE, {"data": json.dumps(command)}, maxlen=STREAM_MAX_LEN, approximate=True
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while (remaining := deadline - loop.time()) > 0:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=min(remaining, 1.0)
            )
            if message is not None:
                payload: dict[str, Any] = json.loads(message["data"])
                return payload
        return None
    finally:
        await pubsub.aclose()
