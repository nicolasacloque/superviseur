import asyncio
import contextlib
import json
import uuid
from typing import Any

from app.collector.config_sync import PointConfigSync
from app.collector.store import PointSettings
from app.common.bus import CHANNEL_POINT_CONFIG
from tests.fakes import FakeStore, make_registry
from tests.helpers import wait_until


async def test_reload_applies_the_new_settings_immediately(redis: Any) -> None:
    registry = make_registry(points=1, poll_interval_s=30)
    point = registry.all_points()[0]
    point.next_due = asyncio.get_running_loop().time() + 3600
    store = FakeStore()
    store.settings[point.ref.point_id] = PointSettings(0.5, 60, 5, "Site/Nouveau/Chemin")
    sync = PointConfigSync(store, redis, registry, default_poll_interval_s=30)

    assert await sync.reload(point.ref.point_id)

    assert (point.deadband, point.max_interval_s, point.poll_interval_s) == (0.5, 60, 5.0)
    assert point.path == "Site/Nouveau/Chemin"
    assert (
        point.next_due <= asyncio.get_running_loop().time() + 5
    )  # intervalle raccourci : appliqué tout de suite


async def test_missing_interval_falls_back_to_the_default(redis: Any) -> None:
    registry = make_registry(points=1)
    point = registry.all_points()[0]
    store = FakeStore()
    store.settings[point.ref.point_id] = PointSettings(0.0, 900, None, None)
    await PointConfigSync(store, redis, registry, default_poll_interval_s=30).reload(
        point.ref.point_id
    )
    assert point.poll_interval_s == 30.0


async def test_unknown_point_is_ignored(redis: Any) -> None:
    sync = PointConfigSync(FakeStore(), redis, make_registry(points=1), 30)
    assert not await sync.reload(uuid.uuid4())


async def test_redis_notification_triggers_a_reload(redis: Any) -> None:
    registry = make_registry(points=1)
    point = registry.all_points()[0]
    store = FakeStore()
    store.settings[point.ref.point_id] = PointSettings(2.5, 120, None, None)
    task = asyncio.create_task(PointConfigSync(store, redis, registry, 30).run())
    try:
        await asyncio.sleep(0.2)  # laisse l'abonnement s'établir
        await redis.publish(CHANNEL_POINT_CONFIG, json.dumps({"point_id": str(point.ref.point_id)}))
        await redis.publish(CHANNEL_POINT_CONFIG, "pas du json")  # message illisible : ignoré
        await wait_until(lambda: point.deadband == 2.5)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
