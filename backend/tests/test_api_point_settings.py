"""Réglage d'un point par un ingénieur : deadband, intervalles, bornes, tags."""

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.common.bus import CHANNEL_POINT_CONFIG
from app.db.models import AuditLog, Point, PointTag
from tests.api_harness import ApiServer, logged_in, make_user, running_api, seed_points

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(db_engine: AsyncEngine, redis: Any, sessions: async_sessionmaker[AsyncSession]):  # type: ignore[no-untyped-def]
    ids = await seed_points(sessions)
    for name, role in [("vera", "viewer"), ("olivia", "operator"), ("edgar", "engineer")]:
        await make_user(sessions, name, role)
    async with running_api(db_engine, redis) as api:
        yield api, ids, sessions, redis


async def patch(api: ApiServer, user: str, point_id: Any, body: dict[str, Any]) -> Any:
    async with logged_in(api, user) as client:
        return await client.patch(f"/points/{point_id}", json=body)


async def test_engineer_tunes_a_point_and_the_change_is_audited_and_announced(env: Any) -> None:
    api, ids, sessions, redis = env
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL_POINT_CONFIG)
    await pubsub.get_message(timeout=1)

    body = {
        "deadband": 0.5,
        "max_interval_s": 300,
        "poll_interval_s": 10,
        "path": "Site/Autre/Temp",
    }
    response = await patch(api, "edgar", ids["Temp soufflage"], body)

    assert response.status_code == 200, response.text
    detail = response.json()
    assert (detail["deadband"], detail["max_interval_s"], detail["poll_interval_s"]) == (
        0.5,
        300,
        10,
    )
    assert detail["path"] == "Site/Autre/Temp" and detail["latest"]["value"] == 21.5

    async with sessions() as session:
        point = await session.get(Point, ids["Temp soufflage"])
        assert point is not None and point.deadband == 0.5 and point.path == "Site/Autre/Temp"
        (row,) = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "point.update"))
        ).all()
    assert row.before == {
        "deadband": 0.0,
        "max_interval_s": 900,
        "poll_interval_s": None,
        "path": "Site/Bat A/CTA-1/Temp soufflage",
    }
    assert row.after == {
        "deadband": 0.5,
        "max_interval_s": 300,
        "poll_interval_s": 10,
        "path": "Site/Autre/Temp",
    }

    # Le collecteur est prévenu pour recharger le réglage sans redémarrer.
    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
    assert json.loads(message["data"]) == {"point_id": str(ids["Temp soufflage"])}


async def test_only_engineers_can_tune_points(env: Any) -> None:
    api, ids, sessions, _ = env
    assert (await patch(api, "vera", ids["Consigne"], {"deadband": 1})).status_code == 403
    assert (await patch(api, "olivia", ids["Consigne"], {"deadband": 1})).status_code == 403
    async with api.client() as anonymous:
        assert (
            await anonymous.patch(f"/points/{ids['Consigne']}", json={"deadband": 1})
        ).status_code == 401
    async with sessions() as session:
        assert (await session.get(Point, ids["Consigne"])).deadband == 0.0
        assert (
            await session.scalars(select(AuditLog).where(AuditLog.action == "point.update"))
        ).all() == []


async def test_invalid_settings_are_rejected(env: Any) -> None:
    api, ids, _, _ = env
    point = ids["Consigne"]  # bornes 10..30 en base
    bodies: list[dict[str, Any]] = [
        {"deadband": -1},
        {"deadband": None},
        {"max_interval_s": 0},
        {"max_interval_s": None},
        {"poll_interval_s": 0},
        {"write_min": 40},  # au-dessus du maximum existant
        {"write_max": 5},  # sous le minimum existant
        {"nom": "x"},  # champ inconnu
        {"tags": ["x"] * 33},
        {"tags": [""]},
    ]
    for body in bodies:
        response = await patch(api, "edgar", point, body)
        assert response.status_code == 422, body
    assert (await patch(api, "edgar", uuid.uuid4(), {"deadband": 1})).status_code == 404


async def test_write_bounds_can_be_moved_and_cleared(env: Any) -> None:
    api, ids, sessions, _ = env
    response = await patch(api, "edgar", ids["Consigne"], {"write_min": 15, "write_max": 25})
    assert (response.json()["write_min"], response.json()["write_max"]) == (15, 25)
    response = await patch(api, "edgar", ids["Consigne"], {"write_min": None, "write_max": None})
    assert response.status_code == 200
    assert (response.json()["write_min"], response.json()["write_max"]) == (None, None)

    # Point sans bornes : l'écriture n'est plus limitée (vérifié par la validation de l'API).
    async with sessions() as session:
        point = await session.get(Point, ids["Consigne"])
        assert point is not None and point.write_min is None and point.write_max is None


async def test_tags_are_replaced_normalized_and_audited(env: Any) -> None:
    api, ids, sessions, _ = env
    response = await patch(
        api, "edgar", ids["Temp soufflage"], {"tags": [" Temp ", "AIR", "air", "zone-1"]}
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["air", "temp", "zone-1"]  # minuscules, sans doublon, triés
    async with sessions() as session:
        tags = (
            await session.scalars(
                select(PointTag.tag).where(PointTag.point_id == ids["Temp soufflage"])
            )
        ).all()
        assert sorted(tags) == ["air", "temp", "zone-1"]
        row = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "point.update"))
        ).one()
    assert row.before == {"tags": ["sensor", "temp"]} and row.after == {
        "tags": ["air", "temp", "zone-1"]
    }

    cleared = await patch(api, "edgar", ids["Temp soufflage"], {"tags": []})
    assert cleared.json()["tags"] == []


async def test_a_no_op_patch_leaves_no_trace(env: Any) -> None:
    api, ids, sessions, redis = env
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL_POINT_CONFIG)
    await pubsub.get_message(timeout=1)

    assert (
        await patch(api, "edgar", ids["Consigne"], {"deadband": 0.0, "tags": ["setpoint"]})
    ).status_code == 200
    assert (await patch(api, "edgar", ids["Consigne"], {})).status_code == 200

    async with sessions() as session:
        assert (
            await session.scalars(select(AuditLog).where(AuditLog.action == "point.update"))
        ).all() == []
    assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3) is None
