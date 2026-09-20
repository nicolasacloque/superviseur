"""Critères d'acceptation du Jalon 3 : écriture jusqu'au simulateur et WebSocket temps réel."""

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from app.auth.tokens import create_token
from app.collector.cov import CovManager
from app.collector.writer import Writer
from app.common.bus import CHANNEL_ALARM_EVENT, point_value_channel
from app.db.models import AuditLog, Device, Point
from tests.api_harness import (
    JWT_SECRET,
    ApiServer,
    cookie_header,
    logged_in,
    make_settings,
    make_user,
    running_api,
)
from tests.harness import Harness

pytestmark = pytest.mark.integration


@dataclass
class Live:
    api: ApiServer
    harness: Harness
    redis: Any
    sessions: async_sessionmaker[AsyncSession]
    writer_task: asyncio.Task[None]

    async def points(self, object_type: str | None = None) -> list[tuple[uuid.UUID, int, int]]:
        """(id du point, instance du device, instance de l'objet)."""
        query = select(Point.id, Device.instance, Point.object_instance).join(
            Device, Point.device_id == Device.id
        )
        if object_type:
            query = query.where(Point.object_type == object_type)
        async with self.sessions() as session:
            return [tuple(row) for row in (await session.execute(query.order_by(Point.id))).all()]

    def sim_object(self, device_instance: int, object_type: str, number: int) -> Any:
        device = next(d for d in self.harness.sim.devices if d.instance == device_instance)
        return device.get_object(object_type, number)


@pytest.fixture
async def live(
    db_engine: Any,
    redis: Any,
    sessions: async_sessionmaker[AsyncSession],
    harness_factory: Any,
) -> AsyncIterator[Live]:
    harness = await harness_factory(devices=5, points=20)  # 100 points
    await harness.discover()
    writer = Writer(harness.driver, harness.store, redis, on_reading=harness.recorder.handle)
    writer_task = asyncio.create_task(writer.run())
    async with running_api(db_engine, redis, make_settings(write_timeout_s=1.5)) as api:
        yield Live(api, harness, redis, sessions, writer_task)
    writer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await writer_task


# -- écriture ----------------------------------------------------------------------------


async def test_write_with_priority_8_reaches_the_simulator_and_can_be_released(live: Live) -> None:
    await make_user(live.sessions, "olivia", "operator")
    (point_id, device, number), *_ = await live.points("analog-value")
    sim = live.sim_object(device, "analog-value", number)

    async with logged_in(live.api, "olivia") as client:
        url = f"/points/{point_id}/write"
        response = await client.post(url, json={"value": 42.5, "priority": 8})
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ok"
        # Visible dans le Priority Array du simulateur, à la priorité 8.
        assert sim.priorityArray[7].null is None
        assert sim.priorityArray[7].real == pytest.approx(42.5)
        assert sim.presentValue == pytest.approx(42.5)

        # Une priorité plus faible n'écrase pas la valeur, mais reprend la main au relâchement.
        assert (await client.post(url, json={"value": 11.0, "priority": 12})).status_code == 200
        assert sim.presentValue == pytest.approx(42.5)
        assert (await client.post(url, json={"value": None, "priority": 8})).status_code == 200
        assert sim.priorityArray[7].null is not None  # relâchement effectif
        assert sim.presentValue == pytest.approx(11.0)

        # Sans priorité précisée : 8, la priorité des opérateurs manuels.
        assert (await client.post(url, json={"value": 30.0})).status_code == 200
        assert sim.priorityArray[7].null is None

        # L'affichage suit sans attendre le prochain polling.
        await live.harness.recorder.flush()
        detail = (await client.get(f"/points/{point_id}")).json()
        assert detail["latest"]["value"] == pytest.approx(30.0)


async def test_write_is_refused_for_viewers_read_only_points_and_out_of_range_values(
    live: Live,
) -> None:
    await make_user(live.sessions, "viola", "viewer")
    await make_user(live.sessions, "olivia", "operator")
    (av_id, av_device, av_number), *_ = await live.points("analog-value")
    (ai_id, _, _), *_ = await live.points("analog-input")
    sim = live.sim_object(av_device, "analog-value", av_number)
    before = sim.presentValue
    async with live.sessions() as session, session.begin():
        await session.execute(
            update(Point).where(Point.id == av_id).values(write_min=10.0, write_max=30.0)
        )

    async with logged_in(live.api, "viola") as viewer:
        response = await viewer.post(f"/points/{av_id}/write", json={"value": 20.0})
        assert response.status_code == 403

    async with logged_in(live.api, "olivia") as operator:
        assert (
            await operator.post(f"/points/{ai_id}/write", json={"value": 1.0})
        ).status_code == 409
        assert (
            await operator.post(f"/points/{av_id}/write", json={"value": 5.0})
        ).status_code == 422
        assert (
            await operator.post(f"/points/{av_id}/write", json={"value": 31.0})
        ).status_code == 422
        assert (
            await operator.post(f"/points/{av_id}/write", json={"value": 20, "priority": 0})
        ).status_code == 422
        assert (
            await operator.post(f"/points/{av_id}/write", json={"value": 20, "priority": 17})
        ).status_code == 422
        assert (
            await operator.post(f"/points/{uuid.uuid4()}/write", json={"value": 1})
        ).status_code == 404
        assert (
            await operator.post(f"/points/{av_id}/write", json={"value": "x"})
        ).status_code == 422
        # Aux bornes : accepté.
        assert (
            await operator.post(f"/points/{av_id}/write", json={"value": 10.0})
        ).status_code == 200

    assert sim.presentValue == pytest.approx(10.0) and before != pytest.approx(10.0)
    async with live.sessions() as session:
        rows = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "point.write"))
        ).all()
    refused = [r for r in rows if r.after and str(r.after["result"]).startswith("refusé")]
    # Tracés par l'API : viewer, non inscriptible, deux bornes, point inconnu. Les priorités
    # hors 1..16 et le type invalide sont rejetés par la validation, avant tout traitement.
    assert len(refused) == 5
    assert any(r.after and r.after["result"] == "ok" for r in rows)  # tracé par le collecteur


async def test_write_times_out_when_the_collector_is_down(live: Live) -> None:
    await make_user(live.sessions, "olivia", "operator")
    (point_id, device, number), *_ = await live.points("analog-value")
    sim = live.sim_object(device, "analog-value", number)
    live.writer_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await live.writer_task

    async with logged_in(live.api, "olivia") as client:
        started = time.monotonic()
        response = await client.post(f"/points/{point_id}/write", json={"value": 20.0})
    assert response.status_code == 504
    assert 1.4 < time.monotonic() - started < 4
    assert sim.priorityArray[7].null is not None  # rien n'a été écrit sur le device


# -- WebSocket ------------------------------------------------------------------------------


async def recv_until(ws: ClientConnection, predicate: Any, within: float = 5.0) -> dict[str, Any]:
    async with asyncio.timeout(within):
        while True:
            message = json.loads(await ws.recv())
            if predicate(message):
                return message  # type: ignore[no-any-return]


async def collect_values(
    ws: ClientConnection, expected: int, within: float = 8.0
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    async with asyncio.timeout(within):
        while len(values) < expected:
            message = json.loads(await ws.recv())
            if message["type"] == "value":
                values[message["point"]] = message
    return values


async def subscribe(ws: ClientConnection, ids: list[uuid.UUID]) -> None:
    await ws.send(json.dumps({"action": "subscribe", "points": [str(i) for i in ids]}))


async def sync(ws: ClientConnection) -> None:
    """Attend la réponse à un ping : les messages envoyés avant ont été traités."""
    await ws.send(json.dumps({"action": "ping"}))
    await recv_until(ws, lambda m: m["type"] == "pong")


async def test_websocket_100_points_updates_and_clean_reconnection(live: Live) -> None:
    await make_user(live.sessions, "vera", "viewer")
    ids = [p[0] for p in await live.points()]
    assert len(ids) == 100
    async with logged_in(live.api, "vera") as client:
        headers = cookie_header(client)

    async with connect(live.api.ws_url, additional_headers=headers) as ws:
        await subscribe(ws, ids)
        # À la souscription : dernière valeur connue de chaque point, immédiatement.
        initial = await collect_values(ws, 100)
        assert set(initial) == {str(i) for i in ids}
        assert all(m["status"] == "ok" and isinstance(m["value"], float) for m in initial.values())

        # Mise à jour temps réel publiée par le collecteur.
        stamp = datetime.now(UTC).isoformat()
        payload = json.dumps({"ts": stamp, "value": 99.5, "status": "ok"})
        await live.redis.publish(point_value_channel(ids[0]), payload)
        update = await recv_until(ws, lambda m: m["type"] == "value" and m["value"] == 99.5)
        assert update == {
            "type": "value",
            "point": str(ids[0]),
            "ts": stamp,
            "value": 99.5,
            "status": "ok",
        }

        # Un point auquel on n'est pas abonné n'arrive pas.
        await live.redis.publish(point_value_channel(uuid.uuid4()), payload)
        # Désabonnement : plus rien pour ce point.
        await ws.send(json.dumps({"action": "unsubscribe", "points": [str(ids[0])]}))
        await sync(ws)
        await live.redis.publish(
            point_value_channel(ids[0]), json.dumps({"ts": stamp, "value": 1.0, "status": "ok"})
        )
        await live.redis.publish(
            point_value_channel(ids[1]), json.dumps({"ts": stamp, "value": 2.0, "status": "ok"})
        )
        following = await recv_until(ws, lambda m: m["type"] == "value")
        assert following["point"] == str(ids[1]) and following["value"] == 2.0

    # Reconnexion propre : le nouveau client reçoit de nouveau l'état complet.
    async with connect(live.api.ws_url, additional_headers=headers) as ws:
        await subscribe(ws, ids)
        assert set(await collect_values(ws, 100)) == {str(i) for i in ids}


async def test_websocket_carries_a_real_cov_change_from_the_simulator(live: Live) -> None:
    await make_user(live.sessions, "vera", "viewer")
    (point_id, device, number), *_ = [p for p in await live.points("analog-input") if p[2] == 1]
    device_state = next(d for d in live.harness.registry.devices.values() if d.instance == device)
    await CovManager(live.harness.driver, live.harness.registry, live.harness.config.cov).service(
        device_state
    )
    async with logged_in(live.api, "vera") as client:
        headers = cookie_header(client)

    async with connect(live.api.ws_url, additional_headers=headers) as ws:
        await subscribe(ws, [point_id])
        await collect_values(ws, 1)
        await asyncio.sleep(0.5)  # notification initiale de l'abonnement COV
        started = time.monotonic()
        live.sim_object(device, "analog-input", number).presentValue = 88.8
        message = await recv_until(
            ws, lambda m: m["type"] == "value" and abs(m["value"] - 88.8) < 0.01, 3
        )
        assert message["point"] == str(point_id) and time.monotonic() - started < 2


async def test_websocket_protocol_errors_limits_and_alarms(live: Live) -> None:
    await make_user(live.sessions, "vera", "viewer")
    async with logged_in(live.api, "vera") as client:
        headers = cookie_header(client)
    async with connect(live.api.ws_url, additional_headers=headers) as ws:
        await ws.send("pas du json")
        assert (await recv_until(ws, lambda m: m["type"] == "error"))[
            "message"
        ] == "message invalide"
        await ws.send(json.dumps({"action": "danser"}))
        assert "inconnue" in (await recv_until(ws, lambda m: m["type"] == "error"))["message"]
        await ws.send(json.dumps({"action": "subscribe", "points": ["pas-un-uuid"]}))
        assert "invalide" in (await recv_until(ws, lambda m: m["type"] == "error"))["message"]

        # Plafond de 500 points par connexion.
        await subscribe(ws, [uuid.uuid4() for _ in range(500)])
        await sync(ws)
        await subscribe(ws, [uuid.uuid4()])
        assert "500" in (await recv_until(ws, lambda m: m["type"] == "error"))["message"]

        # Les alarmes sont relayées à tous les clients connectés.
        await live.redis.publish(
            CHANNEL_ALARM_EVENT, json.dumps({"id": "a1", "state": "active_unacked"})
        )
        alarm = await recv_until(ws, lambda m: m["type"] == "alarm")
        assert alarm["event"]["state"] == "active_unacked"


async def test_websocket_requires_a_valid_session_and_closes_when_the_token_expires(
    live: Live,
) -> None:
    user_id = await make_user(live.sessions, "vera", "viewer")
    with pytest.raises(InvalidStatus) as refused:
        async with connect(live.api.ws_url):
            pass
    assert refused.value.response.status_code == 403
    with pytest.raises(InvalidStatus):
        async with connect(live.api.ws_url, additional_headers={"Cookie": "access_token=faux"}):
            pass

    short_lived = create_token(JWT_SECRET, user_id, "access", timedelta(seconds=2))
    async with connect(
        live.api.ws_url, additional_headers={"Cookie": f"access_token={short_lived}"}
    ) as ws:
        with pytest.raises(ConnectionClosed) as closed:
            async with asyncio.timeout(6):
                while True:
                    await ws.recv()
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401  # à renouveler
