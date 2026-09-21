"""Acquittements : stream Redis `cmd.alarm` -> moteur -> résultat sur `alarm.result.<id>`."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from app.alarms.engine import AckResult, AlarmEngine
from app.alarms.store import AlarmStore
from app.common.bus import ALARM_GROUP, STREAM_ALARM_CMD, alarm_result_channel
from app.common.models import RoleName

log = logging.getLogger(__name__)

# Une commande plus vieille est ignorée : l'opérateur a déjà reçu un échec (délai dépassé).
MAX_COMMAND_AGE_S = 30.0
ACK_ROLES = {RoleName.OPERATOR.value, RoleName.ENGINEER.value, RoleName.ADMIN.value}


class AlarmCommands:
    def __init__(
        self,
        engine: AlarmEngine,
        store: AlarmStore,
        redis: Any,
        consumer: str = "alarms-1",
        max_command_age_s: float = MAX_COMMAND_AGE_S,
    ) -> None:
        self._engine = engine
        self._store = store
        self._redis = redis
        self._consumer = consumer
        self._max_age_s = max_command_age_s

    async def run(self) -> None:
        try:
            await self._redis.xgroup_create(STREAM_ALARM_CMD, ALARM_GROUP, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        cursor = "0"  # d'abord les commandes lues avant un arrêt brutal, puis les nouvelles
        while True:
            entries = await self._redis.xreadgroup(
                ALARM_GROUP, self._consumer, {STREAM_ALARM_CMD: cursor}, count=10, block=1000
            )
            messages = [m for _stream, batch in entries for m in batch]
            if cursor != ">" and not messages:
                cursor = ">"
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
            result = AckResult(False, f"commande invalide: {exc}")
        if command_id:
            payload = {
                "command_id": command_id,
                "status": "ok" if result.ok else "error",
                "message": result.error,
            }
            await self._redis.publish(alarm_result_channel(command_id), json.dumps(payload))
        await self._redis.xack(STREAM_ALARM_CMD, ALARM_GROUP, message_id)

    async def execute(self, command: dict[str, Any]) -> AckResult:
        if command.get("action") != "ack":
            return AckResult(False, f"action inconnue : {command.get('action')}")
        issued_at = command.get("issued_at")
        if issued_at is not None and time.time() - float(issued_at) > self._max_age_s:
            return AckResult(False, "commande expirée")
        user_id = uuid.UUID(str(command["user_id"]))
        # Le rôle est revérifié ici, en plus du contrôle fait par l'API.
        if await self._store.user_role(user_id) not in ACK_ROLES:
            return AckResult(False, "droits insuffisants")
        return await self._engine.acknowledge(uuid.UUID(str(command["event_id"])), user_id)
