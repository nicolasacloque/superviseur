"""Rechargement à chaud du réglage des points (deadband, intervalles) modifié via l'API."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from app.collector.registry import Registry
from app.collector.store import Store
from app.common.bus import CHANNEL_POINT_CONFIG

log = logging.getLogger(__name__)


class PointConfigSync:
    def __init__(
        self, store: Store, redis: Any, registry: Registry, default_poll_interval_s: float
    ) -> None:
        self._store = store
        self._redis = redis
        self._registry = registry
        self._default_poll_interval_s = default_poll_interval_s

    async def reload(self, point_id: uuid.UUID) -> bool:
        """Relit le réglage d'un point en base ; False si le point n'est pas suivi."""
        point = self._registry.point(point_id)
        if point is None:
            return False
        settings = await self._store.get_point_settings(point_id)
        if settings is None:
            return False
        point.deadband = settings.deadband
        point.max_interval_s = settings.max_interval_s
        point.poll_interval_s = float(settings.poll_interval_s or self._default_poll_interval_s)
        point.path = settings.path
        # Un intervalle raccourci s'applique tout de suite, sans attendre l'échéance déjà planifiée.
        loop_now = asyncio.get_running_loop().time()
        if not point.cov_active:
            point.next_due = min(point.next_due, loop_now + point.poll_interval_s)
        log.info("point %s : réglage rechargé", point.name)
        return True

    async def run(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL_POINT_CONFIG)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                try:
                    await self.reload(uuid.UUID(json.loads(message["data"])["point_id"]))
                except Exception:
                    log.warning("réglage de point illisible : %r", message["data"], exc_info=True)
        finally:
            await pubsub.aclose()
