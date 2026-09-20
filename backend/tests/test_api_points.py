"""Points, équipements et historique via l'API."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import Sample
from tests.api_harness import logged_in, make_user, running_api, seed_points

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(db_engine: AsyncEngine, redis: Any, sessions: async_sessionmaker[AsyncSession]):  # type: ignore[no-untyped-def]
    ids = await seed_points(sessions)
    await make_user(sessions, "vera", "viewer")
    async with running_api(db_engine, redis) as api, logged_in(api, "vera") as client:
        yield client, ids, sessions


async def test_networks_and_devices(env: Any) -> None:
    client, ids, _ = env
    networks = (await client.get("/networks")).json()
    assert [n["name"] for n in networks] == ["reseau-test"]

    devices = (await client.get("/devices")).json()
    assert [(d["name"], d["online"], d["point_count"]) for d in devices] == [
        ("CTA-1", True, 3),
        ("VAV-2", False, 1),
    ]
    assert [
        d["name"] for d in (await client.get("/devices", params={"online": "false"})).json()
    ] == ["VAV-2"]

    detail = (await client.get(f"/devices/{ids['device:CTA-1']}")).json()
    assert detail["instance"] == 1 and detail["point_count"] == 3
    assert (await client.get(f"/devices/{uuid.uuid4()}")).status_code == 404


async def test_point_search_filters_and_pagination(env: Any) -> None:
    client, ids, _ = env

    async def names(**params: Any) -> list[str]:
        response = await client.get("/points", params=params)
        assert response.status_code == 200
        return [p["name"] for p in response.json()["items"]]

    assert len(await names()) == 4
    assert await names(path="Site/Bat A/CTA-1") == ["Consigne", "Temp reprise", "Temp soufflage"]
    assert await names(path="Site/Bat B") == ["Ventilo"]
    assert await names(tag="temp") == ["Temp soufflage"]
    assert await names(tag="inexistant") == []
    assert await names(device=str(ids["device:VAV-2"])) == ["Ventilo"]
    assert await names(q="reprise") == ["Temp reprise"]  # nom, sans tenir compte de la casse
    assert await names(q="CTA-1/Cons") == ["Consigne"]  # chemin
    assert sorted(await names(writable="true")) == ["Consigne", "Ventilo"]
    assert await names(q="100%") == []  # les jokers LIKE sont échappés

    page = (await client.get("/points", params={"limit": 2, "offset": 1})).json()
    assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (4, 2, 1, 2)
    assert (await client.get("/points", params={"limit": 501})).status_code == 422


async def test_point_carries_latest_value_and_tags(env: Any) -> None:
    client, ids, _ = env
    item = next(
        p for p in (await client.get("/points")).json()["items"] if p["name"] == "Temp soufflage"
    )
    assert item["latest"]["value"] == 21.5 and item["latest"]["status"] == "ok"
    assert item["tags"] == ["sensor", "temp"] and item["unit"] == "°C"
    assert item["writable"] is False and item["object_type"] == "analog-input"

    detail = (await client.get(f"/points/{ids['Consigne']}")).json()
    assert (detail["write_min"], detail["write_max"], detail["deadband"]) == (10.0, 30.0, 0.0)
    assert detail["max_interval_s"] == 900 and detail["tags"] == ["setpoint"]
    assert (await client.get(f"/points/{uuid.uuid4()}")).status_code == 404


async def test_history_raw_and_bucketed(env: Any) -> None:
    client, ids, sessions = env
    point_id = ids["Temp soufflage"]
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    async with sessions() as session, session.begin():
        session.add_all(
            Sample(
                point_id=point_id, ts=start + timedelta(minutes=10 * i), value=float(i), status="ok"
            )
            for i in range(12)  # 2 h de données, un échantillon toutes les 10 min
        )
    window = {"from": start.isoformat(), "to": (start + timedelta(hours=2)).isoformat()}

    raw = (await client.get(f"/points/{point_id}/history", params=window)).json()
    assert raw["bucket"] is None and not raw["truncated"]
    assert [i["value"] for i in raw["items"]] == [float(i) for i in range(12)]

    hourly = (
        await client.get(f"/points/{point_id}/history", params={**window, "bucket": "1h"})
    ).json()
    assert [(i["min"], i["max"], i["count"]) for i in hourly["items"]] == [
        (0.0, 5.0, 6),
        (6.0, 11.0, 6),
    ]
    assert hourly["items"][0]["value"] == pytest.approx(2.5)  # moyenne de 0..5

    half_hour = (
        await client.get(f"/points/{point_id}/history", params={**window, "bucket": "30m"})
    ).json()
    assert [i["count"] for i in half_hour["items"]] == [3, 3, 3, 3]

    assert (
        await client.get(f"/points/{point_id}/history", params={**window, "bucket": "abc"})
    ).status_code == 422
    assert (
        await client.get(f"/points/{point_id}/history", params={**window, "bucket": "0s"})
    ).status_code == 422
    inverted = {"from": window["to"], "to": window["from"]}
    assert (await client.get(f"/points/{point_id}/history", params=inverted)).status_code == 422
    huge = {"from": "2000-01-01T00:00:00Z", "to": "2030-01-01T00:00:00Z", "bucket": "1s"}
    assert (await client.get(f"/points/{point_id}/history", params=huge)).status_code == 422
    assert (await client.get(f"/points/{uuid.uuid4()}/history")).status_code == 404
