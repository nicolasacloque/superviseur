"""Polling : une boucle par device, lots RPM, détection de perte de communication."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from app.collector.config import CovConfig, PollingConfig
from app.collector.driver_base import (
    DeviceUnreachable,
    Driver,
    DriverError,
    Reading,
)
from app.collector.normalizer import comm_lost_reading
from app.collector.recorder import Recorder
from app.collector.registry import DeviceState, PointState, Registry
from app.collector.store import Store
from app.common.bus import CHANNEL_DEVICE_STATUS
from app.common.models import PointStatus

log = logging.getLogger(__name__)

# Écart minimal entre deux mises à jour de `device.last_seen` en base (secondes).
LAST_SEEN_PERIOD_S = 60.0


def _chunks(items: list[PointState], size: int) -> Iterable[list[PointState]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class Poller:
    def __init__(
        self,
        driver: Driver,
        registry: Registry,
        recorder: Recorder,
        store: Store,
        redis: Any,
        polling: PollingConfig,
        cov: CovConfig,
        tick_s: float = 0.5,
    ) -> None:
        self._driver = driver
        self._registry = registry
        self._recorder = recorder
        self._store = store
        self._redis = redis
        self._polling = polling
        self._cov = cov
        self._tick_s = tick_s

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()

    async def run(self) -> None:
        """Maintient une tâche de polling par device présent dans le registre."""
        tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        try:
            while True:
                devices = self._registry.devices
                for device_id in devices:
                    task = tasks.get(device_id)
                    if task is None or task.done():
                        tasks[device_id] = asyncio.create_task(self._device_loop(device_id))
                for device_id in [d for d in tasks if d not in devices]:
                    tasks.pop(device_id).cancel()
                await asyncio.sleep(1.0)
        finally:
            for task in tasks.values():
                task.cancel()

    async def _device_loop(self, device_id: uuid.UUID) -> None:
        while (device := self._registry.devices.get(device_id)) is not None:
            try:
                due = self._due_points(device)
                if due:
                    await self._poll_cycle(device, due)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception(
                    "device %s : erreur inattendue dans la boucle de polling", device.instance
                )
            await asyncio.sleep(self._tick_s)

    def _interval(self, point: PointState) -> float:
        # Un point sous COV n'est relu que par sécurité (valeurs figées).
        return self._cov.safety_poll_s if point.cov_active else point.poll_interval_s

    def _due_points(self, device: DeviceState) -> list[PointState]:
        now = self._now()
        points = list(device.points.values())
        if not points:
            return []
        for point in points:
            if point.next_due == 0.0:
                # Répartition initiale : les points ne sont pas tous lus à la même seconde.
                point.next_due = now + random.uniform(0.0, self._interval(point))
        interval = self._polling.default_interval_s
        if not device.online:
            # Device hors ligne : une seule lecture de sonde par cycle pour détecter son retour.
            return [points[0]] if now - device.last_attempt >= interval else []
        due = [p for p in points if p.next_due <= now]
        if not due and now - max(device.last_contact, device.last_attempt) >= interval:
            # Rien à lire mais aucun contact récent (cas COV) : sonde de vie.
            return [min(points, key=lambda p: p.next_due)]
        return due

    async def _poll_cycle(self, device: DeviceState, points: list[PointState]) -> None:
        now = self._now()
        device.last_attempt = now
        answered = False
        unreachable = False
        done = 0
        for chunk in _chunks(points, self._polling.batch_size):
            try:
                readings = await self._driver.read([p.ref for p in chunk])
            except DeviceUnreachable:
                unreachable = True
                break
            except DriverError as exc:
                # Le device répond mais refuse la lecture : les points passent en défaut.
                log.warning("device %s : lecture refusée (%s)", device.instance, exc)
                stamp = datetime.now(UTC)
                readings = [
                    Reading(p.ref.point_id, stamp, None, PointStatus.FAULT.value) for p in chunk
                ]
            answered = True
            done += len(chunk)
            for reading in readings:
                await self._recorder.handle(reading)
            for point in chunk:
                point.next_due = now + self._interval(point)

        if unreachable:
            await self._on_failure(device, points[done:])
        elif answered:
            await self._on_success(device)

    async def _on_success(self, device: DeviceState) -> None:
        device.failures = 0
        device.last_contact = self._now()
        if not device.online:
            device.online = True
            log.info("device %s : de nouveau en ligne", device.instance)
            await self._publish_status(device, online=True)
            # Reprise complète : tous les points sont relus et les abonnements COV refaits.
            for point in device.points.values():
                point.next_due = self._now()
                point.cov_active = False
                point.cov_retry_at = 0.0
        elif self._now() - device.last_seen_written >= LAST_SEEN_PERIOD_S:
            await self._touch_last_seen(device)

    async def _on_failure(self, device: DeviceState, points: list[PointState]) -> None:
        device.failures += 1
        log.warning(
            "device %s : sans réponse (cycle %d/%d)",
            device.instance,
            device.failures,
            self._polling.offline_after_cycles,
        )
        now = self._now()
        lost = points
        if device.online and device.failures >= self._polling.offline_after_cycles:
            device.online = False
            await self._publish_status(device, online=False)
            lost = list(device.points.values())
            for point in lost:
                point.cov_active = False
        for point in lost:
            await self._recorder.handle(comm_lost_reading(point.ref.point_id))
            point.next_due = now + self._interval(point)

    async def _touch_last_seen(self, device: DeviceState) -> None:
        device.last_seen_written = self._now()
        try:
            await self._store.set_device_status(device.device_id, True, datetime.now(UTC))
        except Exception:
            log.warning("mise à jour de last_seen impossible", exc_info=True)

    async def _publish_status(self, device: DeviceState, *, online: bool) -> None:
        stamp = datetime.now(UTC)
        device.last_seen_written = self._now()
        try:
            await self._store.set_device_status(device.device_id, online, stamp if online else None)
            await self._redis.publish(
                CHANNEL_DEVICE_STATUS,
                json.dumps(
                    {
                        "device_id": str(device.device_id),
                        "instance": device.instance,
                        "online": online,
                        "ts": stamp.isoformat(),
                    }
                ),
            )
        except Exception:
            log.warning("publication de l'état du device impossible", exc_info=True)
