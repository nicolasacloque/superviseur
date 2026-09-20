"""Normalisation des valeurs BACnet : {point_id, ts UTC, value float, status, raw}."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.collector.driver_base import Reading
from app.common.models import PointStatus

# Types d'objets importés en v1 ; les autres sont ignorés.
IMPORTED_TYPES: tuple[str, ...] = (
    "analog-input",
    "analog-output",
    "analog-value",
    "binary-input",
    "binary-output",
    "binary-value",
    "multi-state-input",
    "multi-state-output",
    "multi-state-value",
)

_UNITS = {
    "degrees-celsius": "°C",
    "degrees-fahrenheit": "°F",
    "kelvin": "K",
    "percent": "%",
    "percent-relative-humidity": "%HR",
    "watts": "W",
    "kilowatts": "kW",
    "megawatts": "MW",
    "watt-hours": "Wh",
    "kilowatt-hours": "kWh",
    "kilojoules": "kJ",
    "megajoules": "MJ",
    "pascals": "Pa",
    "kilopascals": "kPa",
    "hectopascals": "hPa",
    "bars": "bar",
    "volts": "V",
    "amperes": "A",
    "milliamperes": "mA",
    "hertz": "Hz",
    "kilovolt-amperes": "kVA",
    "kilovolt-amperes-reactive": "kvar",
    "cubic-meters-per-hour": "m³/h",
    "cubic-meters-per-second": "m³/s",
    "liters-per-second": "L/s",
    "liters": "L",
    "cubic-meters": "m³",
    "meters": "m",
    "kilograms": "kg",
    "revolutions-per-minute": "tr/min",
    "parts-per-million": "ppm",
    "seconds": "s",
    "minutes": "min",
    "hours": "h",
    "degrees-angular": "°",
    "lux": "lx",
    "ohms": "ohm",
}


def unit_label(name: str | None) -> str | None:
    """Libellé lisible d'une unité BACnet (`degrees-celsius` -> `°C`)."""
    if not name or name == "no-units":
        return None
    return _UNITS.get(name, name)


def is_writable(object_type: str) -> bool:
    """Un point est inscriptible si c'est une sortie ou une valeur."""
    return object_type.endswith(("-output", "-value"))


def is_multistate(object_type: str) -> bool:
    return object_type.startswith("multi-state")


def normalize_present_value(raw: Any) -> float | None:
    """Analogique -> float, binaire -> 0.0/1.0, multi-état -> index entier."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if not isinstance(raw, int | float):
        text = str(raw).lower()
        if text == "active":
            return 1.0
        if text == "inactive":
            return 0.0
        try:
            return float(text)
        except ValueError:
            return None
    return float(raw)


def normalize_status(flags: Sequence[int] | None) -> PointStatus:
    """Statut d'après les 4 bits `status-flags` : in-alarm, fault, overridden, out-of-service."""
    if not flags or len(flags) < 4:
        return PointStatus.OK
    if flags[3]:
        return PointStatus.OUT_OF_SERVICE
    if flags[1]:
        return PointStatus.FAULT
    if flags[2]:
        return PointStatus.OVERRIDDEN
    return PointStatus.OK


def build_reading(
    point_id: uuid.UUID,
    raw_value: Any,
    raw_flags: Sequence[int] | None = None,
    ts: datetime | None = None,
) -> Reading:
    return Reading(
        point_id=point_id,
        ts=ts or datetime.now(UTC),
        value=normalize_present_value(raw_value),
        status=normalize_status(raw_flags).value,
        raw=raw_value,
    )


def comm_lost_reading(point_id: uuid.UUID, ts: datetime | None = None) -> Reading:
    return Reading(point_id, ts or datetime.now(UTC), None, PointStatus.COMM_LOST.value)
