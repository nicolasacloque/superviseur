"""Découverte : Who-Is, inventaire des objets, import en base, mise à jour du registre."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.collector.config import DiscoveryConfig
from app.collector.driver_base import DriverError, FullDriver, ObjectInfo, Reading
from app.collector.registry import Registry
from app.collector.store import Store

log = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    devices: int = 0
    points: int = 0
    # Première valeur de chaque point, lue pendant l'inventaire.
    readings: list[Reading] = field(default_factory=list)


async def run_discovery(
    driver: FullDriver,
    store: Store,
    registry: Registry,
    network_id: uuid.UUID,
    network_name: str,
    config: DiscoveryConfig,
    default_poll_interval_s: float,
) -> DiscoveryResult:
    """Découvre les devices, importe leurs points et recharge le registre.

    Ne supprime jamais rien : un point qui a disparu est seulement marqué `missing`.
    """
    devices = await driver.discover()
    log.info("découverte : %d device(s) répondent", len(devices))
    limit = asyncio.Semaphore(config.parallel_devices)
    inventory: dict[int, list[ObjectInfo]] = {}
    prefixes = tuple(config.ignore_name_prefixes)

    async def import_device(instance: int) -> None:
        info = next(d for d in devices if d.instance == instance)
        async with limit:
            try:
                objects = await driver.describe(info)
            except DriverError as exc:
                log.warning("device %s : inventaire impossible (%s)", instance, exc)
                return
            if prefixes:
                objects = [o for o in objects if not o.name.startswith(prefixes)]
            device_id = await store.upsert_device(network_id, info)
            prefix = f"{network_name}/{info.name or info.instance}"
            await store.upsert_points(device_id, objects, info.supports_cov, prefix)
            inventory[instance] = objects

    await asyncio.gather(*(import_device(d.instance) for d in devices))

    registry.replace(await store.load_devices(network_id, default_poll_interval_s))

    index = {
        (p.ref.device_instance, p.ref.object_type, p.ref.object_instance): p
        for p in registry.all_points()
    }
    now = datetime.now(UTC)
    result = DiscoveryResult(devices=len(inventory))
    for instance, objects in inventory.items():
        for obj in objects:
            point = index.get((instance, obj.object_type, obj.object_instance))
            if point is None:
                continue
            result.points += 1
            if obj.value is not None:
                result.readings.append(Reading(point.ref.point_id, now, obj.value, obj.status))
    log.info("découverte : %d point(s) importés sur %d device(s)", result.points, result.devices)
    return result
