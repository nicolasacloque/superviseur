"""Doublures en mémoire du store, du driver et du registre pour les tests unitaires."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.collector.config import NetworkConfig
from app.collector.driver_base import (
    DeviceInfo,
    DeviceUnreachable,
    ObjectInfo,
    PointRef,
    Reading,
    ReadingCallback,
    RequestRejected,
    WriteResult,
)
from app.collector.registry import DeviceState, PointState, Registry
from app.collector.store import WriteTarget


class FakeStore:
    def __init__(self) -> None:
        self.samples: list[dict[str, Any]] = []
        self.latest: dict[uuid.UUID, dict[str, Any]] = {}
        self.statuses: list[tuple[uuid.UUID, bool]] = []
        self.audit: list[tuple[uuid.UUID | None, str, str, Any, Any]] = []
        self.roles: dict[uuid.UUID, str] = {}
        self.targets: dict[uuid.UUID, WriteTarget] = {}
        self.fail_writes = False

    async def ensure_network(self, config: NetworkConfig) -> uuid.UUID:
        return uuid.uuid4()

    async def upsert_device(self, network_id: uuid.UUID, info: DeviceInfo) -> uuid.UUID:
        return uuid.uuid4()

    async def upsert_points(
        self,
        device_id: uuid.UUID,
        objects: Sequence[ObjectInfo],
        cov_capable: bool,
        path_prefix: str,
    ) -> None:
        return None

    async def load_devices(
        self, network_id: uuid.UUID, default_poll_interval_s: float
    ) -> list[DeviceState]:
        return []

    async def set_device_status(
        self, device_id: uuid.UUID, online: bool, last_seen: datetime | None
    ) -> None:
        self.statuses.append((device_id, online))

    async def write_samples(
        self, samples: Sequence[dict[str, Any]], latest: Sequence[dict[str, Any]]
    ) -> None:
        if self.fail_writes:
            raise OSError("base injoignable")
        self.samples += samples
        for row in latest:
            self.latest[row["point_id"]] = row

    async def get_write_target(self, point_id: uuid.UUID) -> WriteTarget | None:
        return self.targets.get(point_id)

    async def get_user_role(self, user_id: uuid.UUID) -> str | None:
        return self.roles.get(user_id)

    async def add_audit(
        self,
        user_id: uuid.UUID | None,
        action: str,
        target: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        self.audit.append((user_id, action, target, before, after))


class FakeDriver:
    """Driver scriptable : device injoignable, refus de COV, écritures enregistrées."""

    def __init__(self) -> None:
        self.unreachable = False
        self.reject_reads = False
        self.read_calls: list[list[PointRef]] = []
        self.subscriptions: list[uuid.UUID] = []
        self.cov_error: Exception | None = None
        self.writes: list[tuple[PointRef, float | None, int]] = []
        self.write_result = WriteResult(True)
        self.value = 1.0

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def discover(self) -> list[DeviceInfo]:
        return []

    async def describe(self, device: DeviceInfo) -> list[ObjectInfo]:
        return []

    def subscribe(self, callback: ReadingCallback) -> None: ...

    async def read(self, points: list[PointRef]) -> list[Reading]:
        self.read_calls.append(points)
        if self.unreachable:
            raise DeviceUnreachable("muet")
        if self.reject_reads:
            raise RequestRejected("error", "device: operational-problem")
        now = datetime.now(UTC)
        return [Reading(p.point_id, now, self.value, "ok") for p in points]

    async def write(self, point: PointRef, value: float | None, priority: int) -> WriteResult:
        self.writes.append((point, value, priority))
        return self.write_result

    async def subscribe_cov(self, point: PointRef, lifetime_s: int) -> None:
        if self.cov_error is not None:
            raise self.cov_error
        self.subscriptions.append(point.point_id)


def make_registry(
    devices: int = 1,
    points: int = 3,
    *,
    poll_interval_s: float = 0.05,
    cov_capable: bool = False,
) -> Registry:
    states: list[DeviceState] = []
    for d in range(devices):
        device = DeviceState(
            device_id=uuid.uuid4(),
            instance=1000 + d,
            address=f"127.0.0.1:{47900 + d}",
            name=f"dev{d}",
            supports_cov=cov_capable,
        )
        for n in range(1, points + 1):
            point_id = uuid.uuid4()
            device.points[point_id] = PointState(
                ref=PointRef(point_id, device.instance, device.address, "analog-input", n),
                name=f"AI{n}",
                path=f"net/dev{d}/AI{n}",
                deadband=0.0,
                max_interval_s=900,
                poll_interval_s=poll_interval_s,
                cov_capable=cov_capable,
            )
        states.append(device)
    registry = Registry()
    registry.replace(states)
    return registry
