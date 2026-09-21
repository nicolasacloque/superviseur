"""Réception des lectures : publication Redis, règles d'historisation, écriture par lots."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.collector.config import StorageConfig
from app.collector.driver_base import Reading
from app.collector.registry import PointState, Registry
from app.collector.store import Store
from app.common.bus import point_value_channel
from app.common.models import PointStatus

log = logging.getLogger(__name__)

# Garde-fou : au-delà, on abandonne les plus anciens échantillons si la base reste injoignable.
MAX_BUFFERED_ROWS = 200_000


def should_store(point: PointState, reading: Reading) -> bool:
    """Un échantillon est enregistré si la valeur ou le statut change de plus que la deadband,
    ou si `max_interval_s` s'est écoulé depuis le dernier enregistrement (section 5.2)."""
    if not point.stored or point.stored_ts is None:
        return True
    if reading.status != point.stored_status:
        return True
    if (reading.value is None) != (point.stored_value is None):
        return True
    if (
        reading.value is not None
        and point.stored_value is not None
        and abs(reading.value - point.stored_value) > point.deadband
    ):
        return True
    return (reading.ts - point.stored_ts).total_seconds() >= point.max_interval_s


class Recorder:
    def __init__(
        self, store: Store, redis: Any, registry: Registry, storage: StorageConfig
    ) -> None:
        self._store = store
        self._redis = redis
        self._registry = registry
        self._storage = storage
        self._samples: list[dict[str, Any]] = []
        self._latest: dict[Any, dict[str, Any]] = {}
        self._flush_lock = asyncio.Lock()

    async def handle(self, reading: Reading) -> None:
        point = self._registry.point(reading.point_id)
        if point is None:
            return
        if point.device is not None and reading.status != PointStatus.COMM_LOST.value:
            point.device.last_contact = asyncio.get_running_loop().time()
        await self._publish(reading)

        row = {
            "point_id": reading.point_id,
            "ts": reading.ts,
            "value": reading.value,
            "status": reading.status,
        }
        if should_store(point, reading):
            point.stored = True
            point.stored_value = reading.value
            point.stored_status = reading.status
            point.stored_ts = reading.ts
            self._samples.append(row)
        self._latest[reading.point_id] = row
        if len(self._samples) >= self._storage.batch_rows or (
            len(self._latest) >= self._storage.batch_rows
        ):
            await self.flush()

    async def _publish(self, reading: Reading) -> None:
        payload = json.dumps(
            {"ts": reading.ts.isoformat(), "value": reading.value, "status": reading.status}
        )
        try:
            await self._redis.publish(point_value_channel(reading.point_id), payload)
        except Exception:
            log.warning("publication Redis impossible", exc_info=True)

    async def flush(self) -> None:
        """Écrit le tampon en base ; en cas d'échec il est conservé pour le prochain essai."""
        async with self._flush_lock:
            samples, latest = self._samples, list(self._latest.values())
            if not samples and not latest:
                return
            self._samples, self._latest = [], {}
            try:
                await self._store.write_samples(samples, latest)
            except Exception:
                log.exception("écriture de %d échantillons en échec, nouvel essai", len(samples))
                self._samples = (samples + self._samples)[-MAX_BUFFERED_ROWS:]
                for row in latest:
                    self._latest.setdefault(row["point_id"], row)

    async def run(self) -> None:
        """Vide le tampon toutes les `flush_s` secondes."""
        while True:
            await asyncio.sleep(self._storage.flush_s)
            await self.flush()
