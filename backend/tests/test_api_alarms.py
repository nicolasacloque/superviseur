"""API des alarmes : règles (CRUD), liste, acquittement de bout en bout avec le moteur."""

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from websockets.asyncio.client import connect

from app.alarms.commands import AlarmCommands
from app.alarms.engine import AlarmEngine
from app.alarms.store import DbAlarmStore
from app.common.bus import CHANNEL_ALARM_RULES
from app.db.models import AlarmEvent, AlarmRule, AuditLog
from tests.alarm_fakes import Recorder
from tests.api_harness import (
    ApiServer,
    cookie_header,
    logged_in,
    make_settings,
    make_user,
    running_api,
    seed_points,
)

pytestmark = pytest.mark.integration


@dataclass
class Env:
    api: ApiServer
    ids: dict[str, uuid.UUID]
    engine: AlarmEngine
    recorder: Recorder
    sessions: async_sessionmaker[AsyncSession]
    redis: Any
    commands: asyncio.Task[None]


@pytest.fixture
async def env(
    db_engine: AsyncEngine, redis: Any, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Env]:
    ids = await seed_points(sessions)
    for name, role in [("vera", "viewer"), ("olivia", "operator"), ("edgar", "engineer")]:
        await make_user(sessions, name, role)
    store = DbAlarmStore(sessions)
    recorder = Recorder()

    async def publish(channel: str, payload: dict[str, Any]) -> None:
        await redis.publish(channel, json.dumps(payload))

    engine = AlarmEngine(store, publish, recorder)
    commands = asyncio.create_task(AlarmCommands(engine, store, redis).run())
    async with running_api(db_engine, redis, make_settings(write_timeout_s=1.5)) as api:
        yield Env(api, ids, engine, recorder, sessions, redis, commands)
    commands.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await commands


def rule(point: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    return {
        "point_id": str(point),
        "kind": "high",
        "threshold": 28,
        "severity": "critical",
        **overrides,
    }


async def create_rule(env: Env, point: str = "Temp soufflage", **overrides: Any) -> dict[str, Any]:
    async with logged_in(env.api, "edgar") as client:
        response = await client.post("/alarm-rules", json=rule(env.ids[point], **overrides))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def raise_alarm(env: Env, point: str = "Temp soufflage", **overrides: Any) -> dict[str, Any]:
    created = await create_rule(env, point, **overrides)
    await env.engine.load()
    await env.engine.on_value(env.ids[point], 99.0)
    return created


# -- règles ------------------------------------------------------------------------------------


async def test_engineer_creates_lists_reads_and_audits_a_rule(env: Env) -> None:
    pubsub = env.redis.pubsub()
    await pubsub.subscribe(CHANNEL_ALARM_RULES)
    await pubsub.get_message(timeout=1)

    body: dict[str, Any] = {
        "name": "Soufflage trop chaud", "threshold": 28, "hysteresis": 1.5, "delay_s": 30,
        "severity": "warning", "notify": ["email", "email:ops@x.fr", "webhook:https://hook.example/a"],
    }  # fmt: skip
    created = await create_rule(env, **body)
    assert created["path"] == "Site/Bat A/CTA-1/Temp soufflage" and created["enabled"] is True
    assert (created["kind"], created["hysteresis"], created["delay_s"]) == ("high", 1.5, 30)
    assert json.loads(
        (await pubsub.get_message(ignore_subscribe_messages=True, timeout=2))["data"]
    ) == {"reload": True}

    async with logged_in(env.api, "edgar") as client:
        listed = (await client.get("/alarm-rules")).json()
        assert [r["id"] for r in listed] == [created["id"]]
        assert (await client.get("/alarm-rules", params={"enabled": "false"})).json() == []
        assert (await client.get(f"/alarm-rules/{created['id']}")).json()["notify"] == body[
            "notify"
        ]
        assert (await client.get(f"/alarm-rules/{uuid.uuid4()}")).status_code == 404
    async with env.sessions() as session:
        (row,) = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "alarm_rule.create"))
        ).all()
    assert row.after is not None and row.after["threshold"] == 28


async def test_rule_definitions_are_validated(env: Env) -> None:
    point = env.ids["Temp soufflage"]
    invalid: list[dict[str, Any]] = [
        rule(point, threshold=None),  # seuil obligatoire pour high
        rule(point, kind="stale", threshold=0),  # durée positive
        rule(point, kind="state", threshold=None),
        rule(point, hysteresis=-1),
        rule(point, delay_s=-1),
        rule(point, delay_s=999999),
        rule(point, severity="catastrophe"),
        rule(point, kind="inconnu"),
        rule(point, notify=["sms:0600000000"]),
        rule(point, notify=["email:pas-une-adresse"]),
        rule(point, notify=["webhook:ftp://x/y"]),
        rule(point, notify=["webhook:javascript:alert(1)"]),
        rule(point, notify=[f"email:a{i}@x.fr" for i in range(11)]),
        rule(point, inconnu="x"),
    ]
    async with logged_in(env.api, "edgar") as client:
        for body in invalid:
            response = await client.post("/alarm-rules", json=body)
            assert response.status_code == 422, body
        assert (await client.post("/alarm-rules", json=rule(uuid.uuid4()))).status_code == 404
        # Sans seuil : autorisé pour comm_lost et bacnet_event.
        for kind in ("comm_lost", "bacnet_event"):
            ok = await client.post("/alarm-rules", json=rule(point, kind=kind, threshold=None))
            assert ok.status_code == 201, kind
    async with env.sessions() as session:
        assert len((await session.scalars(select(AlarmRule))).all()) == 2


async def test_rules_are_reserved_to_engineers(env: Env) -> None:
    created = await create_rule(env)
    for user in ("vera", "olivia"):
        async with logged_in(env.api, user) as client:
            assert (await client.get("/alarm-rules")).status_code == 403
            assert (
                await client.post("/alarm-rules", json=rule(env.ids["Consigne"]))
            ).status_code == 403
            assert (
                await client.patch(f"/alarm-rules/{created['id']}", json={"enabled": False})
            ).status_code == 403
            assert (await client.delete(f"/alarm-rules/{created['id']}")).status_code == 403
    async with env.api.client() as anonymous:
        assert (await anonymous.get("/alarm-rules")).status_code == 401


async def test_partial_update_is_merged_revalidated_and_audited(env: Env) -> None:
    created = await create_rule(env, delay_s=10, notify=["email:a@x.fr"])
    async with logged_in(env.api, "edgar") as client:
        url = f"/alarm-rules/{created['id']}"
        updated = (await client.patch(url, json={"threshold": 30, "enabled": False})).json()
        assert (updated["threshold"], updated["enabled"], updated["delay_s"]) == (30, False, 10)
        assert updated["notify"] == ["email:a@x.fr"]  # champs absents conservés
        # Changer de type sans fournir de seuil valide reste possible (le seuil existant sert)...
        assert (await client.patch(url, json={"kind": "low"})).json()["kind"] == "low"
        # ... mais l'état final doit être cohérent.
        assert (await client.patch(url, json={"kind": "stale", "threshold": -5})).status_code == 422
        assert (await client.patch(url, json={"notify": ["sms:1"]})).status_code == 422
        assert (await client.patch(url, json={"point_id": str(uuid.uuid4())})).status_code == 422
        assert (
            await client.patch(f"/alarm-rules/{uuid.uuid4()}", json={"enabled": True})
        ).status_code == 404
        # Sans changement : ni audit ni notification du moteur.
        assert (await client.patch(url, json={"threshold": 30})).status_code == 200
    async with env.sessions() as session:
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "alarm_rule.update").order_by(AuditLog.id)
            )
        ).all()
    assert len(rows) == 2
    assert rows[0].before == {"threshold": 28.0, "enabled": True} and rows[0].after == {
        "threshold": 30.0,
        "enabled": False,
    }
    assert rows[1].after == {"kind": "low"}


async def test_a_rule_with_an_open_alarm_cannot_be_deleted(env: Env) -> None:
    created = await raise_alarm(env)
    async with logged_in(env.api, "edgar") as client:
        blocked = await client.delete(f"/alarm-rules/{created['id']}")
        assert blocked.status_code == 409 and "acquittez" in blocked.json()["detail"]
        assert (
            await client.patch(f"/alarm-rules/{created['id']}", json={"enabled": False})
        ).status_code == 200

    await env.engine.load()  # règle désactivée : le moteur clôt l'alarme
    async with logged_in(env.api, "edgar") as client:
        assert (await client.delete(f"/alarm-rules/{created['id']}")).status_code == 204
        assert (await client.delete(f"/alarm-rules/{created['id']}")).status_code == 404
    async with env.sessions() as session:
        assert (await session.scalars(select(AlarmRule))).all() == []
        assert (
            await session.scalars(select(AlarmEvent))
        ).all() == []  # historique clos supprimé avec elle
        assert (
            await session.scalars(select(AuditLog).where(AuditLog.action == "alarm_rule.delete"))
        ).one()


# -- liste -------------------------------------------------------------------------------------


async def test_alarm_listing_filters_and_pagination(env: Env) -> None:
    await raise_alarm(env, "Temp soufflage", severity="critical", name="Haute")
    await raise_alarm(env, "Temp reprise", severity="warning")
    await raise_alarm(env, "Ventilo", kind="state", threshold=1, severity="info")
    await env.engine.on_value(env.ids["Ventilo"], 0.0)  # la condition disparaît : à acquitter

    async with logged_in(env.api, "vera") as client:

        async def names(**params: Any) -> list[str]:
            response = await client.get("/alarms", params=params)
            assert response.status_code == 200, response.text
            return sorted(a["point_name"] for a in response.json()["items"])

        assert await names() == ["Temp reprise", "Temp soufflage", "Ventilo"]  # ouvertes par défaut
        assert await names(state="active") == ["Temp reprise", "Temp soufflage"]
        assert await names(state="unacked") == ["Temp reprise", "Temp soufflage", "Ventilo"]
        assert await names(state="cleared_unacked") == ["Ventilo"]
        assert await names(state="closed") == []
        assert await names(state="all") == ["Temp reprise", "Temp soufflage", "Ventilo"]
        assert await names(severity="critical") == ["Temp soufflage"]
        assert await names(path="Site/Bat A/CTA-1/Temp") == ["Temp reprise", "Temp soufflage"]
        assert await names(path="Site/Bat B") == ["Ventilo"]
        assert await names(point=str(env.ids["Temp reprise"])) == ["Temp reprise"]
        assert (await client.get("/alarms", params={"state": "bizarre"})).status_code == 422

        page = (await client.get("/alarms", params={"limit": 2, "offset": 1})).json()
        assert (page["total"], len(page["items"]), page["limit"], page["offset"]) == (3, 2, 2, 1)

        alarm = next(
            a
            for a in (await client.get("/alarms")).json()["items"]
            if a["point_name"] == "Temp soufflage"
        )
        assert (
            alarm["rule_name"] == "Haute" and alarm["value"] == 99.0 and alarm["threshold"] == 28.0
        )
        assert (
            alarm["state"] == "active_unacked"
            and alarm["unit"] == "°C"
            and alarm["acked_by"] is None
        )
        assert (await client.get(f"/alarms/{alarm['id']}")).json() == alarm
        assert (await client.get(f"/alarms/{uuid.uuid4()}")).status_code == 404
    async with env.api.client() as anonymous:
        assert (await anonymous.get("/alarms")).status_code == 401


# -- acquittement ------------------------------------------------------------------------------


async def test_operator_acknowledges_an_alarm_through_the_engine(env: Env) -> None:
    await raise_alarm(env, notify=["email:ops@x.fr"])
    async with logged_in(env.api, "olivia") as client:
        (alarm,) = (await client.get("/alarms")).json()["items"]
        response = await client.post(f"/alarms/{alarm['id']}/ack")
        assert response.status_code == 200, response.text
        acked = response.json()["alarm"]
        assert (acked["state"], acked["acked_by"]) == ("active_acked", "olivia") and acked[
            "acked_at"
        ]

        again = await client.post(f"/alarms/{alarm['id']}/ack")
        assert (
            again.status_code == 409 and "active_acked" in again.json()["detail"]
        )  # déjà acquittée

        # La condition disparaît : une alarme acquittée se referme d'elle-même.
        await env.engine.on_value(env.ids["Temp soufflage"], 20.0)
        assert (await client.get("/alarms")).json()["items"] == []
        (closed,) = (await client.get("/alarms", params={"state": "closed"})).json()["items"]
        assert closed["id"] == alarm["id"] and closed["cleared_at"]
    assert env.recorder.notified() == ["raised", "acked", "normal"]  # une notification par état

    async with env.sessions() as session:
        rows = (
            await session.scalars(
                select(AuditLog).where(AuditLog.action == "alarm.ack").order_by(AuditLog.id)
            )
        ).all()
    assert [r.after and r.after["result"] for r in rows] == [
        "ok",
        "refusé: acquittement impossible dans l'état active_acked",
    ]


async def test_a_cleared_alarm_is_acknowledged_into_normal(env: Env) -> None:
    await raise_alarm(env)
    await env.engine.on_value(env.ids["Temp soufflage"], 20.0)
    async with logged_in(env.api, "olivia") as client:
        (alarm,) = (await client.get("/alarms")).json()["items"]
        assert alarm["state"] == "cleared_unacked"
        assert (await client.post(f"/alarms/{alarm['id']}/ack")).json()["alarm"][
            "state"
        ] == "normal"
        assert (await client.get("/alarms")).json()["items"] == []


async def test_viewers_and_anonymous_users_cannot_acknowledge(env: Env) -> None:
    await raise_alarm(env)
    async with logged_in(env.api, "vera") as viewer:
        (alarm,) = (await viewer.get("/alarms")).json()["items"]
        assert (await viewer.post(f"/alarms/{alarm['id']}/ack")).status_code == 403
        assert (await viewer.get(f"/alarms/{alarm['id']}")).json()["state"] == "active_unacked"
    async with env.api.client() as anonymous:
        assert (await anonymous.post(f"/alarms/{alarm['id']}/ack")).status_code == 401
    async with logged_in(env.api, "olivia") as operator:
        assert (await operator.post(f"/alarms/{uuid.uuid4()}/ack")).status_code == 404


async def test_acknowledgement_times_out_when_the_alarm_engine_is_down(env: Env) -> None:
    await raise_alarm(env)
    env.commands.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await env.commands
    async with logged_in(env.api, "olivia") as client:
        (alarm,) = (await client.get("/alarms")).json()["items"]
        response = await client.post(f"/alarms/{alarm['id']}/ack")
        assert response.status_code == 504
        assert (await client.get(f"/alarms/{alarm['id']}")).json()["state"] == "active_unacked"


# -- temps réel --------------------------------------------------------------------------------


async def test_alarm_transitions_are_pushed_to_websocket_clients(env: Env) -> None:
    await create_rule(env)
    await env.engine.load()
    async with logged_in(env.api, "vera") as client:
        headers = cookie_header(client)
    async with connect(env.api.ws_url, additional_headers=headers) as ws:
        await asyncio.sleep(0.3)  # abonnement Redis établi
        await env.engine.on_value(env.ids["Temp soufflage"], 50.0)
        message = json.loads(await asyncio.wait_for(ws.recv(), 3))
        assert message["type"] == "alarm"
        assert message["event"]["transition"] == "raised"
        assert message["event"]["event"]["state"] == "active_unacked"
        assert message["event"]["event"]["path"] == "Site/Bat A/CTA-1/Temp soufflage"
