"""Utilitaires de test."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable


async def wait_until(
    condition: Callable[[], bool], within: float = 5.0, interval: float = 0.02
) -> float:
    """Attend qu'une condition devienne vraie ; retourne le temps écoulé ou échoue au délai."""
    start = time.monotonic()
    while not condition():
        if time.monotonic() - start > within:
            raise AssertionError(f"condition non atteinte en {within} s")
        await asyncio.sleep(interval)
    return time.monotonic() - start
