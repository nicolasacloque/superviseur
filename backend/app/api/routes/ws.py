"""WebSocket temps réel : valeurs des points auxquels le client est abonné, alarmes."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.api.deps import ACCESS_COOKIE, CurrentUser, jwt_secret, load_user
from app.auth.tokens import TokenClaims, TokenError, decode_token
from app.common.bus import CHANNEL_ALARM_EVENT, point_value_channel
from app.db.models import PointLatest

log = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

MAX_SUBSCRIPTIONS = 500
CLOSE_UNAUTHORIZED = 4401
_VALUE_PREFIX = point_value_channel("")


class Connection:
    """Une connexion : abonnements Redis dynamiques et envoi sérialisé."""

    def __init__(self, websocket: WebSocket, redis: Any) -> None:
        self.websocket = websocket
        self.pubsub = redis.pubsub()
        self.points: set[uuid.UUID] = set()
        self._send_lock = asyncio.Lock()

    async def send(self, message: dict[str, Any]) -> None:
        async with self._send_lock:
            await self.websocket.send_json(message)


async def _authenticate(websocket: WebSocket) -> tuple[CurrentUser, TokenClaims] | None:
    app = websocket.app
    try:
        claims = decode_token(
            jwt_secret(app.state.settings), websocket.cookies.get(ACCESS_COOKIE), "access"
        )
    except TokenError:
        return None
    async with app.state.sessions() as session:
        user = await load_user(session, claims.user_id, claims.stamp)
    return (user, claims) if user else None


async def _pump(conn: Connection, claims: TokenClaims) -> None:
    """Relaie Redis vers le client ; ferme la connexion quand le jeton d'accès expire."""
    while True:
        message = await conn.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if datetime.now(UTC) >= claims.expires_at:
            # Le client doit renouveler son jeton (POST /auth/refresh) puis se reconnecter.
            await conn.websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        if message is None:
            continue
        channel, data = message["channel"], json.loads(message["data"])
        if channel == CHANNEL_ALARM_EVENT:
            await conn.send({"type": "alarm", "event": data})
        elif channel.startswith(_VALUE_PREFIX):
            await conn.send({"type": "value", "point": channel[len(_VALUE_PREFIX) :], **data})


async def _subscribe(conn: Connection, raw_ids: list[Any], sessions: Any) -> None:
    try:
        wanted = {uuid.UUID(str(raw)) for raw in raw_ids}
    except ValueError:
        await conn.send({"type": "error", "message": "identifiant de point invalide"})
        return
    new = wanted - conn.points
    if len(conn.points) + len(new) > MAX_SUBSCRIPTIONS:
        await conn.send(
            {"type": "error", "message": f"limite de {MAX_SUBSCRIPTIONS} points par connexion"}
        )
        return
    if not new:
        return
    # Abonnement Redis d'abord, lecture de la dernière valeur ensuite : aucune mise à jour perdue.
    await conn.pubsub.subscribe(*(point_value_channel(pid) for pid in new))
    conn.points |= new
    async with sessions() as session:
        rows = (
            await session.scalars(select(PointLatest).where(PointLatest.point_id.in_(new)))
        ).all()
    for row in rows:
        await conn.send(
            {
                "type": "value",
                "point": str(row.point_id),
                "ts": row.ts.isoformat(),
                "value": row.value,
                "status": row.status,
            }
        )


async def _unsubscribe(conn: Connection, raw_ids: list[Any]) -> None:
    try:
        wanted = {uuid.UUID(str(raw)) for raw in raw_ids} & conn.points
    except ValueError:
        await conn.send({"type": "error", "message": "identifiant de point invalide"})
        return
    if wanted:
        await conn.pubsub.unsubscribe(*(point_value_channel(pid) for pid in wanted))
        conn.points -= wanted


@router.websocket("/ws")
async def realtime(websocket: WebSocket) -> None:
    auth = await _authenticate(websocket)
    if auth is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    _user, claims = auth
    await websocket.accept()

    conn = Connection(websocket, websocket.app.state.redis)
    await conn.pubsub.subscribe(CHANNEL_ALARM_EVENT)
    pump = asyncio.create_task(_pump(conn, claims))
    sessions = websocket.app.state.sessions
    try:
        while True:
            try:
                request = json.loads(await websocket.receive_text())
                action = request["action"]
            except (ValueError, KeyError, TypeError):
                await conn.send({"type": "error", "message": "message invalide"})
                continue
            if action == "subscribe":
                await _subscribe(conn, list(request.get("points", [])), sessions)
            elif action == "unsubscribe":
                await _unsubscribe(conn, list(request.get("points", [])))
            elif action == "ping":
                await conn.send({"type": "pong"})
            else:
                await conn.send({"type": "error", "message": f"action inconnue : {action}"})
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        await conn.pubsub.aclose()
