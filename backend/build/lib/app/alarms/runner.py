"""Boucle d'écoute Redis du moteur d'alarmes : valeurs, état des devices, événements BACnet."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from app.alarms.engine import AlarmEngine
from app.common.bus import (
    CHANNEL_ALARM_RULES,
    CHANNEL_BACNET_EVENT,
    CHANNEL_DEVICE_STATUS,
    point_value_channel,
)

log = logging.getLogger(__name__)

_VALUE_PREFIX = point_value_channel("")


class EngineRunner:
    def __init__(self, engine: AlarmEngine, redis: Any) -> None:
        self._engine = engine
        self._redis = redis

    async def run(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe(f"{_VALUE_PREFIX}*")
        await pubsub.subscribe(CHANNEL_DEVICE_STATUS, CHANNEL_BACNET_EVENT, CHANNEL_ALARM_RULES)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                try:
                    await self.dispatch(message["channel"], message["data"])
                except Exception:
                    log.exception("message illisible sur %s", message.get("channel"))
        finally:
            await pubsub.aclose()

    async def dispatch(self, channel: str, data: str) -> None:
        if channel.startswith(_VALUE_PREFIX):
            payload = json.loads(data)
            await self._engine.on_value(
                uuid.UUID(channel[len(_VALUE_PREFIX) :]),
                payload.get("value"),
                payload.get("status", "ok"),
            )
        elif channel == CHANNEL_DEVICE_STATUS:
            payload = json.loads(data)
            await self._engine.on_device_status(
                uuid.UUID(payload["device_id"]), bool(payload["online"])
            )
        elif channel == CHANNEL_BACNET_EVENT:
            payload = json.loads(data)
            await self._engine.on_bacnet_event(
                int(payload["device_instance"]),
                str(payload["object_type"]),
                int(payload["object_instance"]),
                str(payload["to_state"]),
            )
        elif channel == CHANNEL_ALARM_RULES:
            await self._engine.load()
