"""Service collector : `python -m app.collector.main`."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from pathlib import Path

from redis.asyncio import Redis

from app.collector.bacnet_driver import BacnetDriver
from app.collector.config import CollectorConfig, load_config
from app.collector.cov import CovManager
from app.collector.discovery import run_discovery
from app.collector.poller import Poller
from app.collector.recorder import Recorder
from app.collector.registry import Registry
from app.collector.store import DbStore
from app.collector.writer import Writer
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

    async def rediscover() -> None:
        found = await discover()
        while True:
            await asyncio.sleep(config.discovery.interval_s if found else RETRY_DISCOVERY_S)
            try:
                found = await discover()
            except Exception:
                log.exception("découverte en échec")

    poller = Poller(driver, registry, recorder, store, redis, config.polling, config.cov)
    writer = Writer(driver, store, redis, on_reading=recorder.handle)
    jobs = [_heartbeat(), recorder.run(), poller.run(), writer.run(), rediscover()]
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
