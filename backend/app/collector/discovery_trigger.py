"""Relance manuelle de la découverte (`POST /discovery/run`) : signal Redis vers le collecteur."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.common.bus import CHANNEL_DISCOVERY_RUN

log = logging.getLogger(__name__)


class DiscoveryTrigger:
    """Réveille la boucle de redécouverte dès qu'une demande arrive sur le canal Redis."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._event = asyncio.Event()

    async def wait(self, timeout_s: float) -> bool:
        """Attend une demande ou `timeout_s` secondes ; vrai si une demande a été reçue."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout_s)
        except TimeoutError:
            return False
        self._event.clear()
        return True

    async def run(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL_DISCOVERY_RUN)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is not None:
                    log.info("découverte demandée manuellement")
                    self._event.set()
        finally:
            await pubsub.aclose()
