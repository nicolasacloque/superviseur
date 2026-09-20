import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.collector.config import StorageConfig
from app.collector.driver_base import Reading
from app.collector.recorder import Recorder, should_store
from app.common.bus import point_value_channel
from tests.fakes import FakeStore, make_registry

T0 = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


def reading(
    point_id: uuid.UUID, value: float | None, seconds: float = 0, status: str = "ok"
) -> Reading:
    return Reading(point_id, T0 + timedelta(seconds=seconds), value, status)


def test_should_store_rules() -> None:
    registry = make_registry(points=1)
    point = registry.all_points()[0]
    point.deadband = 0.5
    point.max_interval_s = 900
    pid = point.ref.point_id

    assert should_store(point, reading(pid, 20.0))  # première valeur
    point.stored, point.stored_value, point.stored_status, point.stored_ts = True, 20.0, "ok", T0

    assert not should_store(point, reading(pid, 20.3, 10))  # sous la deadband
    assert not should_store(point, reading(pid, 20.5, 10))  # égal à la deadband : pas "plus que"
    assert should_store(point, reading(pid, 20.6, 10))  # au-delà
    assert should_store(point, reading(pid, 19.0, 10))  # dans l'autre sens
    assert should_store(point, reading(pid, 20.0, 10, status="fault"))  # changement de statut
    assert should_store(point, reading(pid, None, 10))  # valeur perdue
    assert not should_store(point, reading(pid, 20.0, 899))
    assert should_store(point, reading(pid, 20.0, 900))  # max_interval atteint


async def test_recorder_publishes_and_buffers_by_deadband(redis: Any) -> None:
    registry = make_registry(points=1)
    point = registry.all_points()[0]
    point.deadband = 1.0
    store = FakeStore()
    recorder = Recorder(store, redis, registry, StorageConfig(batch_rows=500, flush_s=60))
    pid = point.ref.point_id

    pubsub = redis.pubsub()
    await pubsub.subscribe(point_value_channel(pid))
    await pubsub.get_message(timeout=1)  # confirmation d'abonnement

    for i, value in enumerate([10.0, 10.2, 10.4, 12.0]):
        await recorder.handle(reading(pid, value, i))
    await recorder.flush()

    # 4 lectures publiées et présentes dans point_latest, mais seulement 2 échantillons historisés.
    messages = []
    while (m := await pubsub.get_message(timeout=0.2)) is not None:
        messages.append(json.loads(m["data"]))
    assert [m["value"] for m in messages] == [10.0, 10.2, 10.4, 12.0]
    assert [s["value"] for s in store.samples] == [10.0, 12.0]
    assert store.latest[pid]["value"] == 12.0


async def test_recorder_flushes_when_batch_is_full(redis: Any) -> None:
    registry = make_registry(points=5)
    store = FakeStore()
    recorder = Recorder(store, redis, registry, StorageConfig(batch_rows=5, flush_s=60))
    for point in registry.all_points():
        await recorder.handle(reading(point.ref.point_id, 1.0))
    assert len(store.samples) == 5  # écrit sans attendre le flush périodique


async def test_recorder_keeps_buffer_when_database_fails(redis: Any) -> None:
    registry = make_registry(points=1)
    pid = registry.all_points()[0].ref.point_id
    store = FakeStore()
    recorder = Recorder(store, redis, registry, StorageConfig(batch_rows=500, flush_s=60))

    await recorder.handle(reading(pid, 1.0))
    store.fail_writes = True
    await recorder.flush()
    assert store.samples == []

    store.fail_writes = False
    await recorder.flush()  # le tampon conservé est réécrit
    assert [s["value"] for s in store.samples] == [1.0]


async def test_recorder_ignores_unknown_points(redis: Any) -> None:
    store = FakeStore()
    recorder = Recorder(store, redis, make_registry(points=1), StorageConfig())
    await recorder.handle(reading(uuid.uuid4(), 1.0))
    await recorder.flush()
    assert store.samples == [] and store.latest == {}
