from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from app.api.deps import get_engine, get_redis
from app.api.main import create_app


class FakeConnection:
    async def execute(self, _statement: object) -> None:
        return None


class FakeEngine:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[FakeConnection]:
        if self.fail:
            raise OSError("base injoignable")
        yield FakeConnection()


class FakeRedis:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail

    async def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("redis injoignable")
        return True


def _client(*, db_fail: bool = False, redis_fail: bool = False) -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: FakeEngine(fail=db_fail)
    app.dependency_overrides[get_redis] = lambda: FakeRedis(fail=redis_fail)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
async def test_health_ok(path: str) -> None:
    async with _client() as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "services": {"db": "ok", "redis": "ok"}}


async def test_health_degraded_when_db_down() -> None:
    async with _client(db_fail=True) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json()["services"] == {"db": "error", "redis": "ok"}


async def test_health_degraded_when_redis_down() -> None:
    async with _client(redis_fail=True) as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["services"] == {"db": "ok", "redis": "error"}


async def test_openapi_served_under_api_prefix() -> None:
    async with _client() as client:
        response = await client.get("/api/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/health" in response.json()["paths"]
