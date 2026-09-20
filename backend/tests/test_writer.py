import json
import uuid
from typing import Any

import pytest

from app.collector.driver_base import PointRef, WriteResult
from app.collector.store import WriteTarget
from app.collector.writer import Writer
from app.common.bus import STREAM_WRITE, WRITE_GROUP, write_result_channel
from tests.fakes import FakeDriver, FakeStore

OPERATOR, VIEWER = uuid.uuid4(), uuid.uuid4()


def make_writer(redis: Any, **target: Any) -> tuple[Writer, FakeStore, FakeDriver, uuid.UUID]:
    store, driver = FakeStore(), FakeDriver()
    store.roles = {OPERATOR: "operator", VIEWER: "viewer"}
    point_id = uuid.uuid4()
    ref = PointRef(point_id, 1001, "127.0.0.1:47900", "analog-value", 1)
    defaults: dict[str, Any] = {"writable": True, "write_min": None, "write_max": None}
    store.targets[point_id] = WriteTarget(
        ref, path="net/dev/AV1", last_value=20.0, **{**defaults, **target}
    )
    return Writer(driver, store, redis), store, driver, point_id


def command(point_id: uuid.UUID, user: uuid.UUID | None = OPERATOR, **extra: Any) -> dict[str, Any]:
    body = {"command_id": "c1", "point_id": str(point_id), "value": 21.5, "priority": 8}
    body["user_id"] = str(user) if user else None
    return {**body, **extra}


async def test_operator_write_with_priority_is_audited(redis: Any) -> None:
    writer, store, driver, point_id = make_writer(redis)
    result = await writer.execute(command(point_id))
    assert result.ok
    ((ref, value, priority),) = driver.writes
    assert (ref.point_id, value, priority) == (point_id, 21.5, 8)
    ((user, action, target, before, after),) = store.audit
    assert (user, action, target) == (OPERATOR, "point.write", "net/dev/AV1")
    assert before == {"value": 20.0}
    assert after == {"value": 21.5, "priority": 8, "result": "ok"}


async def test_null_value_releases_the_priority(redis: Any) -> None:
    writer, _store, driver, point_id = make_writer(redis)
    assert (await writer.execute(command(point_id, value=None))).ok
    assert driver.writes[0][1] is None


@pytest.mark.parametrize(
    ("override", "target", "expected"),
    [
        ({"user": VIEWER}, {}, "droits insuffisants"),
        ({"user": None}, {}, "utilisateur requis"),
        ({"user": uuid.uuid4()}, {}, "droits insuffisants"),  # utilisateur inconnu
        ({}, {"writable": False}, "point non inscriptible"),
        ({"value": 5.0}, {"write_min": 10.0}, "minimum"),
        ({"value": 50.0}, {"write_max": 30.0}, "maximum"),
        ({"priority": 17}, {}, "priorité"),
        ({"priority": 0}, {}, "priorité"),
    ],
)
async def test_refused_writes_never_reach_the_device(
    redis: Any, override: dict[str, Any], target: dict[str, Any], expected: str
) -> None:
    writer, store, driver, point_id = make_writer(redis, **target)
    user = override.pop("user", OPERATOR)
    result = await writer.execute(command(point_id, user, **override))
    assert not result.ok and expected in (result.error or "")
    assert driver.writes == []
    assert store.audit[0][4]["result"] != "ok"  # le refus est tracé


async def test_unknown_point_is_refused(redis: Any) -> None:
    writer, _store, driver, _point_id = make_writer(redis)
    result = await writer.execute(command(uuid.uuid4()))
    assert not result.ok and "inconnu" in (result.error or "")
    assert driver.writes == []


async def test_device_error_is_reported(redis: Any) -> None:
    writer, store, driver, point_id = make_writer(redis)
    driver.write_result = WriteResult(False, "property: write-access-denied")
    result = await writer.execute(command(point_id))
    assert not result.ok
    assert store.audit[0][4]["result"] == "property: write-access-denied"


async def test_stream_command_is_processed_acknowledged_and_result_published(redis: Any) -> None:
    writer, _store, driver, point_id = make_writer(redis)
    await redis.xgroup_create(STREAM_WRITE, WRITE_GROUP, id="0", mkstream=True)
    pubsub = redis.pubsub()
    await pubsub.subscribe(write_result_channel("c1"))
    await pubsub.get_message(timeout=1)

    await redis.xadd(STREAM_WRITE, {"data": json.dumps(command(point_id))})
    entries = await redis.xreadgroup(WRITE_GROUP, "test", {STREAM_WRITE: ">"}, count=10)
    ((message_id, fields),) = entries[0][1]
    await writer.process(message_id, fields)

    assert len(driver.writes) == 1
    message = await pubsub.get_message(timeout=1)
    assert json.loads(message["data"]) == {"command_id": "c1", "status": "ok", "message": None}
    assert (await redis.xpending(STREAM_WRITE, WRITE_GROUP))["pending"] == 0


async def test_malformed_command_is_acknowledged_without_crashing(redis: Any) -> None:
    writer, _store, driver, _point_id = make_writer(redis)
    await redis.xgroup_create(STREAM_WRITE, WRITE_GROUP, id="0", mkstream=True)
    await redis.xadd(STREAM_WRITE, {"data": "pas du json"})
    entries = await redis.xreadgroup(WRITE_GROUP, "test", {STREAM_WRITE: ">"}, count=10)
    ((message_id, fields),) = entries[0][1]
    await writer.process(message_id, fields)
    assert driver.writes == []
    assert (await redis.xpending(STREAM_WRITE, WRITE_GROUP))["pending"] == 0


async def test_expired_command_is_refused(redis: Any) -> None:
    import time

    writer, store, driver, point_id = make_writer(redis)
    result = await writer.execute(command(point_id, issued_at=time.time() - 60))
    assert not result.ok and "expirée" in (result.error or "")
    assert driver.writes == []
    assert store.audit[0][4]["result"] == "commande expirée"

    fresh = await writer.execute(command(point_id, issued_at=time.time()))
    assert fresh.ok
