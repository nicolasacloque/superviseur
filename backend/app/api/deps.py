"""Dépendances FastAPI : accès aux ressources créées au démarrage."""

from __future__ import annotations

from typing import Any, cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine


def get_engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def get_redis(request: Request) -> Any:
    # Typé Any : les stubs redis-py rendent `await client.ping()` inutilisable sous mypy strict.
    return request.app.state.redis
