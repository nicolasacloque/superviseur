"""État en mémoire du collecteur : devices, points et leur suivi (polling, COV, historisation)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.collector.driver_base import PointRef


@dataclass
class PointState:
    ref: PointRef
    name: str
    path: str | None
    deadband: float
    max_interval_s: int
    poll_interval_s: float
    cov_capable: bool
    device: DeviceState | None = field(default=None, repr=False)
    # Suivi du polling et des abonnements COV (horloge monotone de la boucle asyncio).
    next_due: float = 0.0
    cov_active: bool = False
    cov_expires: float = 0.0
    cov_retry_at: float = 0.0
    # Dernière valeur enregistrée dans l'historique (règles deadband / max_interval).
    stored: bool = False
    stored_value: float | None = None
    stored_status: str | None = None
    stored_ts: datetime | None = None


@dataclass
class DeviceState:
    device_id: uuid.UUID
    instance: int
    address: str
    name: str
    supports_cov: bool
    online: bool = True
    points: dict[uuid.UUID, PointState] = field(default_factory=dict)
    failures: int = 0
    last_contact: float = 0.0
    last_attempt: float = 0.0
    last_seen_written: float = 0.0


class Registry:
    """Index des devices et des points ; `replace` conserve le suivi des points connus."""

    def __init__(self) -> None:
        self.devices: dict[uuid.UUID, DeviceState] = {}
        self._points: dict[uuid.UUID, PointState] = {}

    def replace(self, devices: list[DeviceState]) -> None:
        old_points, old_devices = self._points, self.devices
        for device in devices:
            previous = old_devices.get(device.device_id)
            if previous is not None:
                device.failures = previous.failures
                device.last_contact = previous.last_contact
                device.last_attempt = previous.last_attempt
                device.last_seen_written = previous.last_seen_written
                device.online = previous.online
            for point_id, point in device.points.items():
                point.device = device
                if (old := old_points.get(point_id)) is not None:
                    point.next_due = old.next_due
                    point.cov_active = old.cov_active
                    point.cov_expires = old.cov_expires
                    point.cov_retry_at = old.cov_retry_at
                    point.stored = old.stored
                    point.stored_value = old.stored_value
                    point.stored_status = old.stored_status
                    point.stored_ts = old.stored_ts
        self.devices = {device.device_id: device for device in devices}
        self._points = {pid: p for device in devices for pid, p in device.points.items()}

    def point(self, point_id: uuid.UUID) -> PointState | None:
        return self._points.get(point_id)

    def all_points(self) -> list[PointState]:
        return list(self._points.values())
