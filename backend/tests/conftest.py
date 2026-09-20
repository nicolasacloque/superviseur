"""Fixtures communes : Redis (réel si `TEST_REDIS_URL`, sinon fakeredis) et base de test."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Base

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    if TEST_REDIS_URL:
        client: Any = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
        await client.flushdb()
    else:
        client = FakeAsyncRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Base vierge : schéma créé depuis les modèles (TimescaleDB n'est pas nécessaire ici)."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL non défini")
    assert "test" in (make_url(TEST_DATABASE_URL).database or ""), "base de test requise"
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)
