"""Interface commune des drivers de protocole et types échangés avec le collecteur."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


class DriverError(Exception):
    """Erreur générique d'un driver."""


class DeviceUnreachable(DriverError):
    """Le device n'a pas répondu (timeout après les tentatives)."""


class RequestRejected(DriverError):
    """Le device a répondu par une erreur, un rejet ou un abandon."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class DeviceInfo:
    instance: int
    address: str
    name: str | None = None
    vendor: str | None = None
    model: str | None = None
    supports_rpm: bool = True
    supports_cov: bool = True


@dataclass(frozen=True)
class PointRef:
    """Adresse complète d'un point pour le driver."""

    point_id: uuid.UUID
    device_instance: int
    address: str
    object_type: str
    object_instance: int


@dataclass(frozen=True)
class ObjectInfo:
    """Objet découvert sur un device (avant import en base)."""

    object_type: str
    object_instance: int
    name: str
    description: str | None = None
    unit: str | None = None
    writable: bool = False
    state_text: list[str] | None = None
    value: float | None = None
    status: str = "ok"


@dataclass
class Reading:
    """Lecture normalisée : valeur UTC en flottant, statut, valeur brute."""

    point_id: uuid.UUID
    ts: datetime
    value: float | None
    status: str
    raw: Any = None


@dataclass(frozen=True)
class WriteResult:
    ok: bool
    error: str | None = None


ReadingCallback = Callable[[Reading], Awaitable[None]]


class Driver(Protocol):
    """Interface d'un driver (section 6.4). Seul BACnet est fourni en v1."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def discover(self) -> list[DeviceInfo]: ...

    async def read(self, points: list[PointRef]) -> list[Reading]: ...

    async def write(self, point: PointRef, value: float | None, priority: int) -> WriteResult: ...

    def subscribe(self, callback: ReadingCallback) -> None: ...


class ObjectDriver(Protocol):
    """Extension du driver : inventaire des objets d'un device."""

    async def describe(self, device: DeviceInfo) -> list[ObjectInfo]: ...


class CovDriver(Protocol):
    """Extension du driver : abonnement aux changements de valeur."""

    async def subscribe_cov(self, point: PointRef, lifetime_s: int) -> None: ...


class FullDriver(Driver, ObjectDriver, CovDriver, Protocol):
    """Driver complet attendu par le collecteur."""
