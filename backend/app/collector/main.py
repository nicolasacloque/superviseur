"""Service collector : `python -m app.collector.main`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
from pathlib import Path

from redis.asyncio import Redis

from app.collector.bacnet_driver import BacnetDriver
from app.collector.config import CollectorConfig, load_config
from app.collector.config_sync import PointConfigSync
from app.collector.cov import CovManager
from app.collector.discovery import run_discovery
from app.collector.discovery_trigger import DiscoveryTrigger
from app.collector.driver_base import EventNotice
from app.collector.poller import Poller
from app.collector.recorder import Recorder
from app.collector.registry import Registry
from app.collector.store import DbStore
from app.collector.writer import Writer
from app.common.bus import CHANNEL_BACNET_EVENT
from app.common.config import get_settings
from app.common.logging import configure_logging
from app.db.session import create_engine, create_session_factory

log = logging.getLogger(__name__)

HEARTBEAT_FILE = Path("/tmp/collector.alive")
RETRY_DISCOVERY_S = 60.0


async def _heartbeat() -> None:
    while True:
        await asyncio.to_thread(HEARTBEAT_FILE.touch)
        await asyncio.sleep(10)


async def run(config: CollectorConfig) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    engine = create_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    store = DbStore(create_session_factory(engine))
    registry = Registry()
    recorder = Recorder(store, redis, registry, config.storage)

    driver = BacnetDriver(config.network, config.discovery, config.polling, config.cov)
    await driver.start()
    driver.subscribe(recorder.handle)

    async def publish_event(notice: EventNotice) -> None:
        payload = {
            "device_instance": notice.device_instance,
            "object_type": notice.object_type,
            "object_instance": notice.object_instance,
            "to_state": notice.to_state,
            "from_state": notice.from_state,
            "message": notice.message,
        }
        await redis.publish(CHANNEL_BACNET_EVENT, json.dumps(payload))

    driver.subscribe_events(publish_event)

    network_id = await store.ensure_network(config.network)

    async def discover() -> int:
        result = await run_discovery(
            driver,
            store,
            registry,
            network_id,
            config.network.name,
            config.discovery,
            config.polling.default_interval_s,
        )
        for reading in result.readings:
            await recorder.handle(reading)
        return result.devices

    trigger = DiscoveryTrigger(redis)

    async def rediscover() -> None:
        found = await discover()
        while True:
            # Une demande manuelle interrompt l'attente ; sinon la découverte revient à son rythme.
            await trigger.wait(config.discovery.interval_s if found else RETRY_DISCOVERY_S)
            try:
                found = await discover()
            except Exception:
                log.exception("découverte en échec")

    poller = Poller(driver, registry, recorder, store, redis, config.polling, config.cov)
    writer = Writer(driver, store, redis, on_reading=recorder.handle)
    config_sync = PointConfigSync(store, redis, registry, config.polling.default_interval_s)
    jobs = [
        _heartbeat(),
        recorder.run(),
        poller.run(),
        writer.run(),
        trigger.run(),
        rediscover(),
        config_sync.run(),
    ]
    if config.cov.enabled:
        jobs.append(CovManager(driver, registry, config.cov).run())
    tasks = [asyncio.create_task(job) for job in jobs]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("collecteur démarré (réseau %s)", config.network.name)
    try:
        # Une tâche qui plante arrête le service : Docker le redémarre.
        waiter = asyncio.create_task(stop.wait())
        await asyncio.wait([waiter, *tasks], return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception():
                log.error("tâche arrêtée : %r", task.exception())
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await recorder.flush()
        await driver.stop()
        await redis.aclose()
        await engine.dispose()
        log.info("collecteur arrêté")


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run(load_config()))


if __name__ == "__main__":
    main()
