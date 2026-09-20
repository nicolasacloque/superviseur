import asyncio
import contextlib
import json
from typing import Any

from app.collector.config import CovConfig, PollingConfig, StorageConfig
from app.collector.poller import Poller
from app.collector.recorder import Recorder
from app.collector.registry import Registry
from app.common.bus import CHANNEL_DEVICE_STATUS
from tests.fakes import FakeDriver, FakeStore, make_registry
from tests.helpers import wait_until


def build(
    redis: Any, *, points: int = 3, batch_size: int = 20, cov: CovConfig | None = None
) -> tuple[Registry, FakeStore, FakeDriver, Poller]:
    registry = make_registry(points=points)
    store, driver = FakeStore(), FakeDriver()
    recorder = Recorder(store, redis, registry, StorageConfig(batch_rows=1, flush_s=60))
    polling = PollingConfig(default_interval_s=0.05, batch_size=batch_size, offline_after_cycles=3)
    poller = Poller(
        driver, registry, recorder, store, redis, polling, cov or CovConfig(), tick_s=0.01
    )
    return registry, store, driver, poller


async def run_poller(poller: Poller) -> asyncio.Task[None]:
    return asyncio.create_task(poller.run())


async def stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_polls_all_points_in_batches(redis: Any) -> None:
    registry, store, driver, poller = build(redis, points=5, batch_size=2)
    task = await run_poller(poller)
    try:
        await wait_until(lambda: len(store.latest) == 5)
    finally:
        await stop(task)
    # 5 points par lots de 2 : jamais plus de 2 points par lecture.
    assert max(len(call) for call in driver.read_calls) <= 2
    device = next(iter(registry.devices.values()))
    assert device.online and device.failures == 0


async def test_points_are_spread_not_read_all_at_once(redis: Any) -> None:
    registry, _store, _driver, poller = build(redis, points=50)
    for point in registry.all_points():
        point.poll_interval_s = 30.0
    device = next(iter(registry.devices.values()))
    assert poller._due_points(device) == [] or len(poller._due_points(device)) < 50
    due_times = {round(p.next_due, 1) for p in device.points.values()}
    assert len(due_times) > 5  # échéances étalées sur l'intervalle (gigue)


async def test_device_goes_offline_after_three_failed_cycles_then_recovers(redis: Any) -> None:
    registry, store, driver, poller = build(redis)
    device = next(iter(registry.devices.values()))
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL_DEVICE_STATUS)

    driver.unreachable = True
    task = await run_poller(poller)
    try:
        await wait_until(lambda: not device.online)
        assert device.failures >= 3
        assert (device.device_id, False) in store.statuses
        # Tous les points du device passent en comm_lost, sans valeur.
        await wait_until(
            lambda: (
                all(r["status"] == "comm_lost" for r in store.latest.values())
                and len(store.latest) == 3
            )
        )
        assert all(r["value"] is None for r in store.latest.values())

        driver.unreachable = False  # reprise automatique
        await wait_until(lambda: device.online)
        await wait_until(lambda: all(r["status"] == "ok" for r in store.latest.values()))
    finally:
        await stop(task)

    assert (device.device_id, True) in store.statuses
    events = []
    while (m := await pubsub.get_message(timeout=0.2)) is not None:
        if m["type"] == "message":
            events.append(json.loads(m["data"])["online"])
    assert events == [False, True]


async def test_a_single_failed_cycle_does_not_take_device_offline(redis: Any) -> None:
    registry, store, driver, poller = build(redis)
    device = next(iter(registry.devices.values()))
    driver.unreachable = True
    task = await run_poller(poller)
    try:
        await wait_until(lambda: device.failures >= 1)
        driver.unreachable = False
        await wait_until(lambda: device.failures == 0 and len(store.latest) == 3)
    finally:
        await stop(task)
    assert device.online
    assert all(online for _device_id, online in store.statuses)  # jamais passé hors ligne


async def test_refused_reads_mark_points_in_fault_but_device_stays_online(redis: Any) -> None:
    registry, store, driver, poller = build(redis)
    device = next(iter(registry.devices.values()))
    driver.reject_reads = True
    task = await run_poller(poller)
    try:
        await wait_until(lambda: len(store.latest) == 3)
    finally:
        await stop(task)
    assert all(r["status"] == "fault" for r in store.latest.values())
    assert device.online and device.failures == 0


async def test_cov_points_are_only_polled_for_safety(redis: Any) -> None:
    cov = CovConfig(safety_poll_s=3600)
    registry, store, driver, poller = build(redis, cov=cov)
    for point in registry.all_points():
        point.cov_active = True
    task = await run_poller(poller)
    try:
        await asyncio.sleep(0.4)
    finally:
        await stop(task)
    # Un point sous COV n'est pas relu à chaque intervalle ; seule la sonde de vie
    # (un point par cycle de 0,05 s) subsiste pour détecter la perte du device.
    assert all(len(call) == 1 for call in driver.read_calls)
    assert store.samples  # la sonde alimente quand même l'historique
