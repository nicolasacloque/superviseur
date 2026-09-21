"""Politiques TimescaleDB pilotées par la configuration (rétention)."""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)


async def has_timescale(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        found = await conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"))
    return found is not None


async def sample_is_hypertable(engine: AsyncEngine) -> bool:
    if not await has_timescale(engine):
        return False
    async with engine.connect() as conn:
        found = await conn.scalar(
            text(
                "SELECT 1 FROM timescaledb_information.hypertables "
                "WHERE hypertable_schema = 'public' AND hypertable_name = 'sample'"
            )
        )
    return found is not None


async def sync_retention(engine: AsyncEngine, retention_days: int) -> None:
    """Aligne la rétention de `sample` sur `RETENTION_DAYS` (0 = conservation illimitée)."""
    if not await sample_is_hypertable(engine):
        log.info("sample n'est pas une hypertable : rétention non appliquée")
        return
    async with engine.begin() as conn:
        await conn.execute(text("SELECT remove_retention_policy('sample', if_exists => true)"))
        if retention_days > 0:
            await conn.execute(
                text("SELECT add_retention_policy('sample', make_interval(days => :days))"),
                {"days": retention_days},
            )
    log.info(
        "rétention de l'historique : %s",
        f"{retention_days} jours" if retention_days else "illimitée",
    )
