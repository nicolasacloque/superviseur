"""Écoute Redis du moteur d'alarmes et commandes d'acquittement."""

import asyncio
import contextlib
import json
import time
import uuid
from typing import Any

from app.alarms.commands import AlarmCommands
from app.alarms.runner import EngineRunner
from app.common.bus import (
    ALARM_GROUP,
    CHANNEL_ALARM_RULES,
    CHANNEL_BACNET_EVENT,
    CHANNEL_DEVICE_STATUS,
    STREAM_ALARM_CMD,
    alarm_result_channel,
    point_value_channel,
)
from tests.alarm_fakes import build, make_rule
from tests.helpers import wait_until

OPERATOR, VIEWER = uuid.uuid4(), uuid.uuid4()


async def raised_event(engine: Any, store: Any, rule: Any) -> uuid.UUID:
    await engine.load()
    await engine.on_value(rule.point_id, 30)
    (event,) = store.events.values()
    return event.id  # type: ignore[no-any-return]


def command(event_id: uuid.UUID, user: uuid.UUID = OPERATOR, **extra: Any) -> dict[str, Any]:
    return {
        "command_id": "c1",
        "action": "ack",
        "event_id": str(event_id),
        "user_id": str(user),
        **extra,
    }


class TestAcknowledgeCommands:
    async def test_an_operator_acknowledges_through_the_stream(self, redis: Any) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        store.roles, store.logins = {OPERATOR: "operator"}, {OPERATOR: "olivia"}
        event_id = await raised_event(engine, store, rule)
        commands = AlarmCommands(engine, store, redis)

        await redis.xgroup_create(STREAM_ALARM_CMD, ALARM_GROUP, id="0", mkstream=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(alarm_result_channel("c1"))
        await pubsub.get_message(timeout=1)
        await redis.xadd(STREAM_ALARM_CMD, {"data": json.dumps(command(event_id))})
        entries = await redis.xreadgroup(ALARM_GROUP, "t", {STREAM_ALARM_CMD: ">"}, count=10)
        ((message_id, fields),) = entries[0][1]
        await commands.process(message_id, fields)

        assert store.events[event_id].state == "active_acked"
        result = json.loads((await pubsub.get_message(timeout=1))["data"])
        assert result == {"command_id": "c1", "status": "ok", "message": None}
        assert (await redis.xpending(STREAM_ALARM_CMD, ALARM_GROUP))["pending"] == 0

    async def test_the_role_is_rechecked_by_the_engine(self, redis: Any) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        store.roles = {VIEWER: "viewer"}
        event_id = await raised_event(engine, store, rule)
        commands = AlarmCommands(engine, store, redis)
        for user in (VIEWER, uuid.uuid4()):  # lecteur, utilisateur inconnu
            result = await commands.execute(command(event_id, user))
            assert not result.ok and "droits" in (result.error or "")
        assert store.events[event_id].state == "active_unacked"

    async def test_expired_unknown_and_malformed_commands_are_refused(self, redis: Any) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        store.roles = {OPERATOR: "operator"}
        event_id = await raised_event(engine, store, rule)
        commands = AlarmCommands(engine, store, redis)

        stale = await commands.execute(command(event_id, issued_at=time.time() - 120))
        assert not stale.ok and "expirée" in (stale.error or "")
        assert not (await commands.execute({**command(event_id), "action": "delete"})).ok
        assert store.events[event_id].state == "active_unacked"

        await redis.xgroup_create(STREAM_ALARM_CMD, ALARM_GROUP, id="0", mkstream=True)
        await redis.xadd(STREAM_ALARM_CMD, {"data": "pas du json"})
        entries = await redis.xreadgroup(ALARM_GROUP, "t", {STREAM_ALARM_CMD: ">"}, count=10)
        ((message_id, fields),) = entries[0][1]
        await commands.process(message_id, fields)  # ne plante pas, acquitte le message
        assert (await redis.xpending(STREAM_ALARM_CMD, ALARM_GROUP))["pending"] == 0


class TestEngineRunner:
    async def test_dispatch_routes_each_channel_to_the_engine(self, redis: Any) -> None:
        by_point, by_device, by_object = (
            make_rule("high", 28),
            make_rule("comm_lost", None),
            make_rule("bacnet_event", None),
        )
        engine, store, rec, _ = build(by_point, by_device, by_object)
        await engine.load()
        runner = EngineRunner(engine, redis)

        await runner.dispatch(
            point_value_channel(by_point.point_id),
            json.dumps({"ts": "x", "value": 31, "status": "ok"}),
        )
        await runner.dispatch(
            CHANNEL_DEVICE_STATUS,
            json.dumps({"device_id": str(by_device.device_id), "online": False}),
        )
        await runner.dispatch(
            CHANNEL_BACNET_EVENT,
            json.dumps(
                {
                    "device_instance": 1001,
                    "object_type": "analog-input",
                    "object_instance": 1,
                    "to_state": "high-limit",
                }
            ),
        )
        assert rec.transitions() == ["raised", "raised", "raised"]

        store.rules.append(make_rule("low", 1))
        await runner.dispatch(CHANNEL_ALARM_RULES, json.dumps({"reload": True}))
        assert len(engine.states()) == 4  # les règles ont été rechargées

    async def test_live_messages_reach_the_engine_through_redis(self, redis: Any) -> None:
        rule = make_rule("high", 28)
        engine, store, rec, _ = build(rule)
        await engine.load()
        task = asyncio.create_task(EngineRunner(engine, redis).run())
        try:
            await asyncio.sleep(0.2)  # abonnement établi
            await redis.publish(
                point_value_channel(rule.point_id),
                json.dumps({"ts": "x", "value": 35.0, "status": "ok"}),
            )
            await redis.publish(
                point_value_channel(uuid.uuid4()),
                json.dumps({"ts": "x", "value": 99.0, "status": "ok"}),
            )
            await redis.publish(point_value_channel(rule.point_id), "message illisible")
            await wait_until(lambda: bool(rec.published))
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        assert rec.transitions() == ["raised"]  # la règle n'a réagi qu'à la valeur qui la concerne
