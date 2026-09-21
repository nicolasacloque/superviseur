"""Utilitaires de test."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from alembic.config import Config


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


BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(url: str) -> Config:
    """Configuration Alembic pointée sur la base de test."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config
