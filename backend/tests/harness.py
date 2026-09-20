"""Banc d'intégration : simulateur BACnet + driver réel + base + Redis."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from bacnet_sim import Simulator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collector.bacnet_driver import BacnetDriver
from app.collector.config import CollectorConfig
from app.collector.discovery import DiscoveryResult, run_discovery
from app.collector.recorder import Recorder
from app.collector.registry import Registry
from app.collector.store import DbStore

SIM_PORT = 47950
COLLECTOR_PORT = 47940


@dataclass
class Harness:
    sim: Simulator
    driver: BacnetDriver
    store: DbStore
    registry: Registry
    recorder: Recorder
    config: CollectorConfig
    redis: Any

    async def discover(self) -> DiscoveryResult:
        network_id = await self.store.ensure_network(self.config.network)
        result = await run_discovery(
            self.driver,
            self.store,
            self.registry,
            network_id,
            self.config.network.name,
            self.config.discovery,
            self.config.polling.default_interval_s,
        )
        for reading in result.readings:
            await self.recorder.handle(reading)
        await self.recorder.flush()
        return result

    async def close(self) -> None:
        await self.driver.stop()
        self.sim.stop()
        await asyncio.sleep(0.1)  # laisse les sockets UDP se fermer avant le test suivant


async def build_harness(
    sessions: async_sessionmaker[AsyncSession],
    redis: Any,
    *,
    devices: int = 3,
    points: int = 20,
    animate: bool = False,
) -> Harness:
    config = CollectorConfig.model_validate(
        {
            "network": {"bind_ip": "127.0.0.1", "port": COLLECTOR_PORT},
            "discovery": {
                "timeout_s": 1.0,
                "targets": [f"127.0.0.1:{SIM_PORT}-{SIM_PORT + devices - 1}"],
            },
            "polling": {"default_interval_s": 0.3, "timeout_s": 0.5, "retries": 0},
            "cov": {"lifetime_s": 60},
            "storage": {"flush_s": 0.2},
        }
    )
    sim = Simulator(devices=devices, points_per_device=points, base_port=SIM_PORT)
    sim.start(animate=animate, tick_s=0.5)
    driver = BacnetDriver(config.network, config.discovery, config.polling, config.cov)
    await driver.start()
    store = DbStore(sessions)
    registry = Registry()
    recorder = Recorder(store, redis, registry, config.storage)
    driver.subscribe(recorder.handle)
    return Harness(sim, driver, store, registry, recorder, config, redis)
