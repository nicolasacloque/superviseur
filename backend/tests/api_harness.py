"""Banc d'intégration de l'API : vrai serveur uvicorn dans la boucle du test."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import uvicorn
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.api.main import create_app
from app.auth.passwords import hash_password
from app.common.config import Settings
from app.common.models import RoleName
from app.db.models import Device, Network, Point, PointLatest, PointTag, Role, User

API_PORT = 47960
PASSWORD = "mot-de-passe-de-test"
JWT_SECRET = "test-secret-" + "x" * 32


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "jwt_secret": SecretStr(JWT_SECRET),
        "cookie_secure": False,  # le serveur de test parle HTTP
        "write_timeout_s": 3.0,
    }
    return Settings(_env_file=None, **{**values, **overrides})


@dataclass
class ApiServer:
    base_url: str
    ws_url: str
    settings: Settings

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url + "/api/v1", timeout=15)


@asynccontextmanager
async def running_api(
    engine: AsyncEngine, redis: Any, settings: Settings | None = None
) -> AsyncIterator[ApiServer]:
    resolved = settings or make_settings()
    app = create_app(settings=resolved, engine=engine, redis=redis)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=API_PORT, log_level="warning", lifespan="on"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.02)
    try:
        yield ApiServer(
            f"http://127.0.0.1:{API_PORT}", f"ws://127.0.0.1:{API_PORT}/api/v1/ws", resolved
        )
    finally:
        server.should_exit = True
        await task


async def ensure_roles(session: AsyncSession) -> dict[str, int]:
    roles = {r.name: r.id for r in (await session.scalars(select(Role))).all()}
    if not roles:
        session.add_all([Role(name=r.value) for r in RoleName])
        await session.flush()
        roles = {r.name: r.id for r in (await session.scalars(select(Role))).all()}
    return roles


async def make_user(
    sessions: async_sessionmaker[AsyncSession], login: str, role: str, *, active: bool = True
) -> uuid.UUID:
    async with sessions() as session, session.begin():
        roles = await ensure_roles(session)
        user = User(
            login=login, password_hash=hash_password(PASSWORD), role_id=roles[role], active=active
        )
        session.add(user)
        await session.flush()
        return user.id


@asynccontextmanager
async def logged_in(
    api: ApiServer, login_name: str, password: str = PASSWORD
) -> AsyncIterator[httpx.AsyncClient]:
    """Client déjà connecté (les cookies sont conservés par httpx)."""
    async with api.client() as client:
        response = await client.post(
            "/auth/login", json={"login": login_name, "password": password}
        )
        assert response.status_code == 200, response.text
        yield client


def cookie_header(client: httpx.AsyncClient) -> dict[str, str]:
    return {"Cookie": "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar)}


async def seed_points(
    sessions: async_sessionmaker[AsyncSession],
) -> dict[str, uuid.UUID]:
    """Deux équipements, quelques points, tags, valeurs : jeu de données sans BACnet."""
    now = datetime.now(UTC)
    async with sessions() as session, session.begin():
        network = Network(name="reseau-test", bind_ip="127.0.0.1", port=47808)
        session.add(network)
        await session.flush()
        ahu = Device(network_id=network.id, instance=1, name="CTA-1", address="a1", online=True)
        vav = Device(network_id=network.id, instance=2, name="VAV-2", address="a2", online=False)
        session.add_all([ahu, vav])
        await session.flush()
        specs = [
            (
                ahu,
                "analog-input",
                1,
                "Temp soufflage",
                "Site/Bat A/CTA-1/Temp soufflage",
                False,
                21.5,
            ),
            (ahu, "analog-input", 2, "Temp reprise", "Site/Bat A/CTA-1/Temp reprise", False, 22.0),
            (ahu, "analog-value", 1, "Consigne", "Site/Bat A/CTA-1/Consigne", True, 20.0),
            (vav, "binary-output", 1, "Ventilo", "Site/Bat B/VAV-2/Ventilo", True, 1.0),
        ]
        ids: dict[str, uuid.UUID] = {}
        for device, kind, number, name, path, writable, value in specs:
            point = Point(
                device_id=device.id,
                object_type=kind,
                object_instance=number,
                name=name,
                description=f"{name} (test)",
                unit="°C" if kind.startswith("analog") else None,
                writable=writable,
                path=path,
                write_min=10.0 if name == "Consigne" else None,
                write_max=30.0 if name == "Consigne" else None,
            )
            session.add(point)
            await session.flush()
            ids[name] = point.id
            session.add(PointLatest(point_id=point.id, ts=now, value=value, status="ok"))
        session.add_all(
            [
                PointTag(point_id=ids["Temp soufflage"], tag="temp"),
                PointTag(point_id=ids["Temp soufflage"], tag="sensor"),
                PointTag(point_id=ids["Consigne"], tag="setpoint"),
            ]
        )
        ids["device:CTA-1"], ids["device:VAV-2"], ids["network"] = ahu.id, vav.id, network.id
    return ids
