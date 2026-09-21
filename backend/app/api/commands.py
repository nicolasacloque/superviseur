"""Envoi d'une commande au collecteur ou au moteur d'alarmes, avec attente du résultat."""

from __future__ import annotations

import asyncio
import json
from typing import Any

STREAM_MAX_LEN = 10_000


async def send_and_wait(
    redis: Any,
    stream: str,
    result_channel: str,
    command: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any] | None:
    """Publie la commande dans le stream et attend son résultat ; `None` au délai dépassé."""
    pubsub = redis.pubsub()
    # Abonnement avant l'envoi : le résultat ne peut pas arriver avant qu'on l'écoute.
    await pubsub.subscribe(result_channel)
    try:
        await redis.xadd(
            stream, {"data": json.dumps(command)}, maxlen=STREAM_MAX_LEN, approximate=True
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
