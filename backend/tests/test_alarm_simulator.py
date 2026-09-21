"""Chaque type de règle contre le simulateur BACnet : du contrôleur simulé jusqu'à l'alarme en base.

Chaîne réelle : simulateur -> driver BACnet -> Redis -> moteur d'alarmes -> base et notifications.
"""

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alarms.engine import AlarmEngine
from app.alarms.runner import EngineRunner
from app.alarms.store import DbAlarmStore
from app.collector.cov import CovManager
from app.collector.driver_base import EventNotice
from app.collector.poller import Poller
from app.collector.registry import DeviceState, PointState
from app.common.bus import CHANNEL_BACNET_EVENT
from app.db.models import AlarmEvent, AlarmRule
from tests.alarm_fakes import Recorder
from tests.harness import COLLECTOR_PORT, Harness

pytestmark = pytest.mark.integration


@dataclass
class Stack:
    harness: Harness
    engine: AlarmEngine
    recorder: Recorder
    sessions: async_sessionmaker[AsyncSession]
    redis: Any
    background: list[asyncio.Task[Any]]

    def point(self, instance: int, object_type: str, number: int) -> PointState:
        return next(
            p
            for p in self.harness.registry.all_points()
            if (p.ref.device_instance, p.ref.object_type, p.ref.object_instance)
            == (instance, object_type, number)
        )

    def device(self, instance: int) -> DeviceState:
        return next(d for d in self.harness.registry.devices.values() if d.instance == instance)

    def sim(self, instance: int, object_type: str, number: int) -> Any:
        device = next(d for d in self.harness.sim.devices if d.instance == instance)
        return device.get_object(object_type, number)

    async def add_rule(self, point: PointState, **fields: Any) -> uuid.UUID:
        values: dict[str, Any] = {
            "kind": "high", "severity": "warning", "notify": ["email:ops@x.fr"], **fields,
        }  # fmt: skip
        async with self.sessions() as session, session.begin():
            rule = AlarmRule(point_id=point.ref.point_id, **values)
            session.add(rule)
            await session.flush()
            rule_id = rule.id
        await self.engine.load()
        return rule_id

    async def events(self) -> list[AlarmEvent]:
        async with self.sessions() as session:
            rows = await session.scalars(select(AlarmEvent).order_by(AlarmEvent.raised_at))
            return list(rows)

    async def states(self) -> list[str]:
        return [e.state for e in await self.events()]

    async def until(self, expected: list[str], within: float = 8.0) -> None:
        """Attend que la liste des états d'alarme en base devienne `expected`."""
        deadline = time.monotonic() + within
        while (states := await self.states()) != expected:
            assert time.monotonic() < deadline, f"états en base : {states}, attendu : {expected}"
            await asyncio.sleep(0.05)


@pytest.fixture
async def stack(
    harness_factory: Any, sessions: async_sessionmaker[AsyncSession], redis: Any
) -> AsyncIterator[Stack]:
    harness: Harness = await harness_factory(devices=2, points=20)
    await harness.discover()
    recorder = Recorder()

    async def publish(channel: str, payload: dict[str, Any]) -> None:
        await redis.publish(channel, json.dumps(payload))

    engine = AlarmEngine(DbAlarmStore(sessions), publish, recorder)

    async def publish_event(notice: EventNotice) -> None:  # ce que fait le service collector
        payload = {
            "device_instance": notice.device_instance, "object_type": notice.object_type,
            "object_instance": notice.object_instance, "to_state": notice.to_state,
        }  # fmt: skip
        await redis.publish(CHANNEL_BACNET_EVENT, json.dumps(payload))

    harness.driver.subscribe_events(publish_event)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(0.2)
            await engine.tick()

    background = [
        asyncio.create_task(EngineRunner(engine, redis).run()),
        asyncio.create_task(ticker()),
    ]
    await asyncio.sleep(0.3)  # abonnements Redis établis
    yield Stack(harness, engine, recorder, sessions, redis, background)
    for task in background:
        task.cancel()
    for task in background:
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def subscribe_cov(stack: Stack, instance: int) -> None:
    await CovManager(
        stack.harness.driver, stack.harness.registry, stack.harness.config.cov
    ).service(stack.device(instance))
    await asyncio.sleep(0.4)  # notification initiale de l'abonnement


async def test_high_rule_raises_and_clears_from_a_simulated_controller(stack: Stack) -> None:
    point = stack.point(1001, "analog-input", 1)
    sim = stack.sim(1001, "analog-input", 1)
    threshold = float(sim.presentValue) + 5
    await stack.add_rule(point, kind="high", threshold=threshold, hysteresis=1.0, name="Haute")
    await subscribe_cov(stack, 1001)
    assert await stack.states() == []  # sous le seuil : aucune alarme

    sim.presentValue = threshold + 2
    await stack.until(["active_unacked"])
    (event,) = await stack.events()
    assert event.raised_value == pytest.approx(threshold + 2, abs=0.01)

    sim.presentValue = threshold - 0.5  # sous le seuil mais dans l'hystérésis : maintenue
    await asyncio.sleep(0.6)
    assert await stack.states() == ["active_unacked"]

    sim.presentValue = threshold - 3
    await stack.until(["cleared_unacked"])
    assert stack.recorder.notified() == [
        "raised",
        "cleared",
    ]  # une notification par changement d'état


async def test_low_rule(stack: Stack) -> None:
    point = stack.point(1001, "analog-input", 2)
    sim = stack.sim(1001, "analog-input", 2)
    await stack.add_rule(point, kind="low", threshold=float(sim.presentValue) - 4)
    await subscribe_cov(stack, 1001)
    sim.presentValue = float(sim.presentValue) - 10
    await stack.until(["active_unacked"])


async def test_state_rule_on_a_binary_input(stack: Stack) -> None:
    point = stack.point(1001, "binary-input", 1)
    sim = stack.sim(1001, "binary-input", 1)
    sim.presentValue = "inactive"
    await stack.add_rule(point, kind="state", threshold=1, severity="critical")
    await subscribe_cov(stack, 1001)
    assert await stack.states() == []
    sim.presentValue = "active"  # défaut
    await stack.until(["active_unacked"])
    sim.presentValue = "inactive"
    await stack.until(["cleared_unacked"])


async def test_stale_rule_raises_when_a_value_stops_arriving_and_clears_when_it_returns(
    stack: Stack,
) -> None:
    point = stack.point(1002, "analog-input", 3)
    await stack.add_rule(point, kind="stale", threshold=1)  # aucune valeur depuis 1 s
    await stack.until(["active_unacked"])  # rien ne relit ce point : il est figé

    (reading,) = await stack.harness.driver.read([point.ref])  # une lecture fraîche arrive
    await stack.harness.recorder.handle(reading)
    await stack.until(["cleared_unacked"], within=3)


async def test_comm_lost_rule_follows_the_device(stack: Stack) -> None:
    point = stack.point(1002, "analog-input", 1)
    await stack.add_rule(point, kind="comm_lost", severity="critical")
    poller = Poller(
        stack.harness.driver, stack.harness.registry, stack.harness.recorder, stack.harness.store,
        stack.redis, stack.harness.config.polling, stack.harness.config.cov, tick_s=0.05,
    )  # fmt: skip
    stack.background.append(asyncio.create_task(poller.run()))
    await asyncio.sleep(0.5)
    assert await stack.states() == []  # le device répond

    stack.harness.sim.devices[1].faults.muted = True
    await stack.until(["active_unacked"], within=15)
    stack.harness.sim.devices[1].faults.muted = False
    await stack.until(["cleared_unacked"], within=15)
    assert stack.recorder.notified() == ["raised", "cleared"]


async def test_bacnet_event_rule_follows_the_controller_notifications(stack: Stack) -> None:
    point = stack.point(1001, "analog-input", 4)
    await stack.add_rule(point, kind="bacnet_event", severity="critical")
    controller = stack.harness.sim.devices[0]
    collector = f"127.0.0.1:{COLLECTOR_PORT}"

    controller.send_event(collector, "analog-input", 4, "high-limit")
    await stack.until(["active_unacked"])
    controller.send_event(collector, "analog-input", 7, "high-limit")  # autre objet : sans effet
    controller.send_event(collector, "analog-input", 4, "normal", "high-limit")
    await stack.until(["cleared_unacked"])
    assert stack.recorder.notified() == ["raised", "cleared"]


async def test_the_delay_holds_back_a_short_excursion_then_lets_a_lasting_one_through(
    stack: Stack,
) -> None:
    point = stack.point(1001, "analog-input", 5)
    sim = stack.sim(1001, "analog-input", 5)
    normal = float(sim.presentValue)
    await stack.add_rule(point, kind="high", threshold=normal + 5, delay_s=1)
    await subscribe_cov(stack, 1001)

    sim.presentValue = normal + 8  # dépassement bref : revenu avant 1 s
    await asyncio.sleep(0.3)
    sim.presentValue = normal
    await asyncio.sleep(1.5)
    assert await stack.states() == [] and stack.recorder.notifications == []

    started = time.monotonic()
    sim.presentValue = normal + 8  # dépassement qui dure
    await stack.until(["active_unacked"])
    assert time.monotonic() - started >= 0.9  # la temporisation a été respectée
