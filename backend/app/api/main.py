"""Point d'entrée FastAPI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.health import router as health_router
from app.common.config import get_settings
from app.common.logging import configure_logging
from app.db.session import create_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url)
    app.state.engine = engine
    app.state.redis = redis
    try:
        yield
    finally:
        await redis.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Superviseur BACnet/IP",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.include_router(health_router, prefix="/api/v1")
    # Alias à la racine pour les healthchecks Docker et la supervision externe.
    app.include_router(health_router, include_in_schema=False)
    return app


app = create_app()
