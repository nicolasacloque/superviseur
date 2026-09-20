"""Collecteur contre le simulateur BACnet, avec une vraie base et Redis (critères du Jalon 2)."""

import asyncio
import contextlib
import json
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collector.cov import CovManager
from app.collector.poller import Poller
from app.collector.writer import Writer
from app.common.bus import point_value_channel
from app.db.models import AuditLog, Device, Point, PointLatest, Role, User
from tests.helpers import wait_until

pytestmark = pytest.mark.integration


async def count(sessions: async_sessionmaker[AsyncSession], model: Any, *where: Any) -> int:
    async with sessions() as session:
        return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


async def start(coro: Any) -> asyncio.Task[Any]:
    return asyncio.create_task(coro)


async def stop(*tasks: asyncio.Task[Any]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_discovery_imports_2000_points_in_under_3_minutes(
    harness_factory: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    harness = await harness_factory(devices=10, points=200)

    started = time.monotonic()
    result = await harness.discover()
    elapsed = time.monotonic() - started

    assert elapsed < 180, f"import en {elapsed:.0f} s"
    assert (result.devices, result.points) == (10, 2000)
    assert await count(sessions, Device) == 10
    assert await count(sessions, Point) == 2000
    # Toutes les valeurs sont déjà à jour dans point_latest.
    assert await count(sessions, PointLatest) == 2000

    async with sessions() as session:
        rows = (
            await session.execute(
                select(Point.object_type, func.count()).group_by(Point.object_type)
            )
        ).all()
        kinds = {row[0]: row[1] for row in rows}
        assert kinds["analog-input"] == 500 and kinds["multi-state-value"] == 300
        # Un point est inscriptible si c'est une sortie ou une valeur.
        writable = {
            p.object_type
            for p in (await session.scalars(select(Point).where(Point.writable))).all()
        }
        assert writable == {
            "analog-output",
            "analog-value",
            "binary-output",
            "binary-value",
            "multi-state-value",
        }
        celsius = await session.scalar(select(Point).where(Point.unit == "°C").limit(1))
        assert (
            celsius is not None
            and celsius.path
            and celsius.path.startswith("reseau-principal/SIM-")
        )
        msv = await session.scalar(
            select(Point).where(Point.object_type == "multi-state-value").limit(1)
        )
        assert msv is not None and msv.state_text == ["Arret", "Reduit", "Confort", "Boost"]


async def test_rediscovery_never_deletes_points_it_only_marks_them_missing(
    harness_factory: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    harness = await harness_factory(devices=1, points=20)
    await harness.discover()
    # Le device n'expose plus un objet.
    device = harness.sim.devices[0]
    obj = device.get_object("analog-input", 1)
    device.app.delete_object(obj)

    await harness.discover()

    assert await count(sessions, Point) == 20  # rien n'est supprimé
    assert await count(sessions, Point, Point.missing.is_(True)) == 1
    assert len(harness.registry.all_points()) == 19  # et il n'est plus scruté


async def test_polling_keeps_point_latest_up_to_date(
    harness_factory: Any, sessions: async_sessionmaker[AsyncSession], redis: Any
) -> None:
    harness = await harness_factory(devices=3, points=20)
    await harness.discover()
    poller = Poller(
        harness.driver, harness.registry, harness.recorder, harness.store, redis,
        harness.config.polling, harness.config.cov, tick_s=0.05,
    )  # fmt: skip
    flusher = await start(harness.recorder.run())
    task = await start(poller.run())
    try:
        target = harness.sim.devices[1].get_object("analog-input", 3)
        target.presentValue = 77.7
        point = next(
            p
            for p in harness.registry.all_points()
            if p.ref.device_instance == 1002
            and p.ref.object_instance == 3
            and p.ref.object_type == "analog-input"
        )

        async def latest() -> float | None:
            async with sessions() as session:
                return await session.scalar(
                    select(PointLatest.value).where(PointLatest.point_id == point.ref.point_id)
                )

        for _ in range(100):
            if await latest() == pytest.approx(77.7, abs=0.01):
                break
            await asyncio.sleep(0.1)
        assert await latest() == pytest.approx(77.7, abs=0.01)
    finally:
        await stop(task, flusher)


async def test_cov_change_reaches_redis_in_under_2_seconds(
    harness_factory: Any, redis: Any
) -> None:
    harness = await harness_factory(devices=1, points=20)
    await harness.discover()
    device = next(iter(harness.registry.devices.values()))
    manager = CovManager(harness.driver, harness.registry, harness.config.cov)
    await manager.service(device)
    assert all(p.cov_active for p in device.points.values())

    point = next(
        p
        for p in device.points.values()
        if p.ref.object_type == "analog-input" and p.ref.object_instance == 2
    )
    pubsub = redis.pubsub()
    await pubsub.subscribe(point_value_channel(point.ref.point_id))
    await pubsub.get_message(timeout=1)
    await asyncio.sleep(0.5)  # laisse passer la notification initiale de l'abonnement
    while await pubsub.get_message(timeout=0.1):
        pass

    started = time.monotonic()
    harness.sim.devices[0].get_object("analog-input", 2).presentValue = 55.5
    while True:
        message = await pubsub.get_message(timeout=2.5)
        assert message is not None, "aucune notification COV reçue"
        payload = json.loads(message["data"])
        if payload["value"] == pytest.approx(55.5, abs=0.01):
            break
    assert time.monotonic() - started < 2.0


async def test_muted_device_goes_comm_lost_then_recovers_automatically(
    harness_factory: Any, sessions: async_sessionmaker[AsyncSession], redis: Any
) -> None:
    harness = await harness_factory(devices=2, points=10)
    await harness.discover()
    poller = Poller(
        harness.driver, harness.registry, harness.recorder, harness.store, redis,
        harness.config.polling, harness.config.cov, tick_s=0.05,
    )  # fmt: skip
    flusher = await start(harness.recorder.run())
    task = await start(poller.run())
    muted = next(d for d in harness.registry.devices.values() if d.instance == 1002)
    healthy = next(d for d in harness.registry.devices.values() if d.instance == 1001)
    try:
        harness.sim.devices[1].faults.muted = True
        started = time.monotonic()
        await wait_until(lambda: not muted.online, within=20)
        # 3 cycles de 0,3 s d'attente + 0,5 s de timeout chacun, avec marge.
        assert time.monotonic() - started < 6
        assert healthy.online  # l'autre device n'est pas affecté

        ids = [p.ref.point_id for p in muted.points.values()]

        async def statuses() -> set[str]:
            async with sessions() as session:
                rows = await session.scalars(
                    select(PointLatest.status).where(PointLatest.point_id.in_(ids))
                )
                return set(rows.all())

        for _ in range(50):
            if await statuses() == {"comm_lost"}:
                break
            await asyncio.sleep(0.1)
        assert await statuses() == {"comm_lost"}
        async with sessions() as session:
            assert (
                await session.scalar(select(Device.online).where(Device.instance == 1002)) is False
            )

        harness.sim.devices[1].faults.muted = False  # le device revient
        await wait_until(lambda: muted.online, within=20)
        for _ in range(50):
            if await statuses() == {"ok"}:
                break
            await asyncio.sleep(0.1)
        assert await statuses() == {"ok"}
    finally:
        await stop(task, flusher)


async def test_device_without_rpm_falls_back_to_read_property(
    harness_factory: Any, sessions: async_sessionmaker[AsyncSession], redis: Any
) -> None:
    harness = await harness_factory(devices=2, points=10)
    # Device 1 : n'annonce pas RPM. Device 2 : l'annonce mais le rejette ensuite.
    harness.sim.devices[0].faults.no_rpm = True
    await harness.discover()
    assert await count(sessions, Point) == 20  # l'import fonctionne sans RPM
    harness.sim.devices[1].faults.no_rpm = True

    for device in harness.sim.devices:
        refs = [
            p.ref for p in harness.registry.all_points() if p.ref.device_instance == device.instance
        ]
        harness.sim.devices[0].get_object("analog-input", 1).presentValue = 31.0
        readings = await harness.driver.read(refs)
        assert len(readings) == 10 and all(r.status == "ok" for r in readings)

    ai1 = next(
        p
        for p in harness.registry.all_points()
        if p.ref.device_instance == 1001
        and p.ref.object_type == "analog-input"
        and p.ref.object_instance == 1
    )
    (reading,) = await harness.driver.read([ai1.ref])
    assert reading.value == pytest.approx(31.0, abs=0.01)


async def test_object_list_is_read_by_index_without_segmentation(
    harness_factory: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    harness = await harness_factory(devices=1, points=20)
    harness.sim.devices[0].faults.no_segmentation = True
    result = await harness.discover()
    assert result.points == 20 and await count(sessions, Point) == 20


async def test_write_with_priority_is_visible_in_priority_array_and_released(
    harness_factory: Any, sessions: async_sessionmaker[AsyncSession], redis: Any
) -> None:
    harness = await harness_factory(devices=1, points=40)
    await harness.discover()
    async with sessions() as session, session.begin():
        roles = {r.name: r.id for r in (await session.scalars(select(Role))).all()} or {}
        if not roles:
            session.add_all([Role(name=n) for n in ("viewer", "operator", "engineer", "admin")])
            await session.flush()
            roles = {r.name: r.id for r in (await session.scalars(select(Role))).all()}
        operator = User(login="op", password_hash="x", role_id=roles["operator"], active=True)
        viewer = User(login="vw", password_hash="x", role_id=roles["viewer"], active=True)
        session.add_all([operator, viewer])
        await session.flush()
        operator_id, viewer_id = operator.id, viewer.id
        av = (
            await session.scalars(select(Point).where(Point.object_type == "analog-value").limit(1))
        ).one()
        ai = (
            await session.scalars(select(Point).where(Point.object_type == "analog-input").limit(1))
        ).one()
        av_id, av_instance, ai_id = av.id, av.object_instance, ai.id

    writer = Writer(harness.driver, harness.store, redis, on_reading=harness.recorder.handle)
    sim_object = harness.sim.devices[0].get_object("analog-value", av_instance)

    def command(point_id: uuid.UUID, user: uuid.UUID, value: float | None) -> dict[str, Any]:
        return {
            "command_id": "c",
            "point_id": str(point_id),
            "user_id": str(user),
            "value": value,
            "priority": 8,
        }

    result = await writer.execute(command(av_id, operator_id, 42.5))
    assert result.ok, result.error
    assert sim_object.priorityArray[7].null is None  # priorité 8 occupée
    assert sim_object.presentValue == pytest.approx(42.5)

    result = await writer.execute(command(av_id, operator_id, None))  # relâchement
    assert result.ok, result.error
    assert sim_object.priorityArray[7].null is not None
    assert sim_object.presentValue != pytest.approx(42.5)

    refused = await writer.execute(command(av_id, viewer_id, 10.0))
    assert not refused.ok and "droits" in (refused.error or "")
    assert sim_object.priorityArray[7].null is not None  # rien n'a été écrit

    refused = await writer.execute(command(ai_id, operator_id, 10.0))
    assert not refused.ok and "inscriptible" in (refused.error or "")

    # Chaque tentative, acceptée ou refusée, est tracée.
    assert await count(sessions, AuditLog, AuditLog.action == "point.write") == 4
