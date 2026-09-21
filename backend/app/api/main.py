"""Point d'entrée FastAPI."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import jwt_secret
from app.api.health import router as health_router
from app.api.routes import alarms, audit, auth, devices, points, synoptics, write, ws
from app.common.config import Settings, get_settings
from app.common.logging import configure_logging
from app.db.session import create_engine, create_session_factory
from app.db.timescale import has_timescale, sync_retention

log = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    redis: Any | None = None,
) -> FastAPI:
    """Crée l'application ; `engine` et `redis` peuvent être injectés (tests)."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level)
        jwt_secret(resolved)  # échoue au démarrage si le secret est absent ou trop court
        own_engine = engine or create_engine(resolved.database_url)
        own_redis = redis or Redis.from_url(resolved.redis_url, decode_responses=True)
        app.state.settings = resolved
        app.state.engine = own_engine
        app.state.redis = own_redis
        app.state.sessions = create_session_factory(own_engine)
        app.state.use_time_bucket = False
        try:
            app.state.use_time_bucket = await has_timescale(own_engine)
            await sync_retention(own_engine, resolved.retention_days)
        except Exception:
            log.exception("politiques TimescaleDB non appliquées")
        try:
            yield
        finally:
            if redis is None:
                await own_redis.aclose()
            if engine is None:
                await own_engine.dispose()

    app = FastAPI(
        title="Superviseur BACnet/IP",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    api = APIRouter(prefix="/api/v1")
    for module in (auth, devices, points, write, alarms, synoptics, audit):
        api.include_router(module.router)
    api.include_router(health_router)
    api.include_router(ws.router)
    app.include_router(api)
    # Alias à la racine pour les healthchecks Docker et la supervision externe.
    app.include_router(health_router, include_in_schema=False)
    if resolved.frontend_dir and Path(resolved.frontend_dir).is_dir():
        # Monté en dernier : les routes de l'API et /api/docs restent prioritaires.
        app.mount("/", StaticFiles(directory=resolved.frontend_dir, html=True), name="frontend")
    return app


app = create_app()
