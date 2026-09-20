"""Écritures de consignes : lecture du stream Redis `cmd.write`, contrôle, écriture BACnet."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.collector.driver_base import Driver, Reading, WriteResult
from app.collector.store import Store
from app.common.bus import STREAM_WRITE, WRITE_GROUP, write_result_channel
from app.common.models import RoleName

log = logging.getLogger(__name__)

DEFAULT_PRIORITY = 8
# Une commande plus vieille que ça n'est pas exécutée : l'opérateur a déjà reçu un échec.
MAX_COMMAND_AGE_S = 30.0
WRITER_ROLES = {RoleName.OPERATOR.value, RoleName.ENGINEER.value, RoleName.ADMIN.value}


class Writer:
    def __init__(
        self,
        driver: Driver,
        store: Store,
        redis: Any,
        on_reading: Callable[[Reading], Awaitable[None]] | None = None,
        consumer: str = "collector-1",
        max_command_age_s: float = MAX_COMMAND_AGE_S,
    ) -> None:
        self._driver = driver
        self._store = store
        self._redis = redis
        self._on_reading = on_reading
        self._consumer = consumer
        self._max_age_s = max_command_age_s

    async def run(self) -> None:
        try:
            await self._redis.xgroup_create(STREAM_WRITE, WRITE_GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        # D'abord les commandes lues avant un arrêt brutal et jamais acquittées, puis les nouvelles.
        pending_id = "0"
        while True:
            entries = await self._redis.xreadgroup(
                WRITE_GROUP, self._consumer, {STREAM_WRITE: pending_id}, count=10, block=1000
            )
            messages = [m for _stream, batch in entries for m in batch]
            if pending_id != ">" and not messages:
                pending_id = ">"
                continue
            for message_id, fields in messages:
                await self.process(message_id, fields)

    async def process(self, message_id: str, fields: dict[str, str]) -> None:
        command_id = ""
        try:
            command = json.loads(fields["data"])
            command_id = str(command["command_id"])
            result = await self.execute(command)
        except Exception as exc:
            log.warning("commande %s invalide : %s", message_id, exc)
            result = WriteResult(False, f"commande invalide: {exc}")
        if command_id:
            payload = {
                "command_id": command_id,
                "status": "ok" if result.ok else "error",
                "message": result.error,
            }
            await self._redis.publish(write_result_channel(command_id), json.dumps(payload))
        await self._redis.xack(STREAM_WRITE, WRITE_GROUP, message_id)

    async def execute(self, command: dict[str, Any]) -> WriteResult:
        """Valide une commande puis l'exécute ; tout refus est tracé dans `audit_log`."""
        point_id = uuid.UUID(str(command["point_id"]))
        raw_value = command.get("value")
        value = None if raw_value is None else float(raw_value)
        priority = DEFAULT_PRIORITY if command.get("priority") is None else int(command["priority"])
        user_id = uuid.UUID(str(command["user_id"])) if command.get("user_id") else None

        target = await self._store.get_write_target(point_id)
        issued_at = command.get("issued_at")
        if issued_at is not None and time.time() - float(issued_at) > self._max_age_s:
            refusal: str | None = "commande expirée"
        else:
            refusal = await self._refusal(target, value, priority, user_id)
        before = {"value": target.last_value} if target else None
        after: dict[str, Any] = {"value": value, "priority": priority}
        result = WriteResult(False, refusal)
        if refusal is None and target is not None:
            result = await self._driver.write(target.ref, value, priority)
        after["result"] = "ok" if result.ok else (result.error or "error")
        await self._store.add_audit(
            user_id,
            "point.write",
            target.path or str(point_id) if target else str(point_id),
            before,
            after,
        )
        if result.ok and target is not None and self._on_reading is not None:
            await self._refresh(target.ref)
        return result

    async def _refusal(
        self, target: Any, value: float | None, priority: int, user_id: uuid.UUID | None
    ) -> str | None:
        if user_id is None:
            return "utilisateur requis"
        if await self._store.get_user_role(user_id) not in WRITER_ROLES:
            return "droits insuffisants"
        if target is None:
            return "point inconnu"
        if not target.writable:
            return "point non inscriptible"
        if not 1 <= priority <= 16:
            return "priorité hors de 1..16"
        if value is not None:
            if target.write_min is not None and value < target.write_min:
                return f"valeur inférieure au minimum ({target.write_min})"
            if target.write_max is not None and value > target.write_max:
                return f"valeur supérieure au maximum ({target.write_max})"
        return None

    async def _refresh(self, ref: Any) -> None:
        """Relit le point juste après l'écriture pour que l'affichage suive sans attendre."""
        try:
            readings = await asyncio.wait_for(self._driver.read([ref]), 10)
            for reading in readings:
                if self._on_reading is not None:
                    await self._on_reading(reading)
        except Exception:
            log.debug("relecture après écriture impossible", exc_info=True)
