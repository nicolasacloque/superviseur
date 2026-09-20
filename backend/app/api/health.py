"""Endpoint d'état des services."""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.deps import get_engine, get_redis

log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

CHECK_TIMEOUT_S = 2.0


class HealthStatus(BaseModel):
    status: Literal["ok", "degraded"]
    services: dict[str, Literal["ok", "error"]]


async def _check_db(engine: AsyncEngine) -> bool:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        log.warning("health: base de données injoignable", exc_info=True)
        return False
    return True


async def _check_redis(client: Any) -> bool:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_S):
            await client.ping()
    except Exception:
        log.warning("health: redis injoignable", exc_info=True)
        return False
    return True


@router.get("/health", response_model=HealthStatus)
async def health(
    response: Response,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    redis: Annotated[Any, Depends(get_redis)],
) -> HealthStatus:
    """200 si tous les services répondent, 503 sinon."""
    db_ok, redis_ok = await asyncio.gather(_check_db(engine), _check_redis(redis))
    services: dict[str, Literal["ok", "error"]] = {
        "db": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
    }
    if db_ok and redis_ok:
        return HealthStatus(status="ok", services=services)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthStatus(status="degraded", services=services)
