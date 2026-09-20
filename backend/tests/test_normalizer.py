import uuid

import pytest
from bacpypes3.basetypes import BinaryPV, StatusFlags

from app.collector.normalizer import (
    build_reading,
    comm_lost_reading,
    is_writable,
    normalize_present_value,
    normalize_status,
    unit_label,
)
from app.common.models import PointStatus


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (21.5, 21.5),
        (3, 3.0),
        (BinaryPV("active"), 1.0),
        (BinaryPV("inactive"), 0.0),
        ("active", 1.0),
        (True, 1.0),
        (None, None),
        ("n'importe quoi", None),
    ],
)
def test_normalize_present_value(raw: object, expected: float | None) -> None:
    assert normalize_present_value(raw) == expected


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ([0, 0, 0, 0], PointStatus.OK),
        ([1, 0, 0, 0], PointStatus.OK),  # in-alarm seul : la valeur reste valide
        ([0, 1, 0, 0], PointStatus.FAULT),
        ([0, 0, 1, 0], PointStatus.OVERRIDDEN),
        ([0, 1, 0, 1], PointStatus.OUT_OF_SERVICE),  # out-of-service l'emporte
        (None, PointStatus.OK),
        ([], PointStatus.OK),
    ],
)
def test_normalize_status(flags: list[int] | None, expected: PointStatus) -> None:
    assert normalize_status(flags) == expected


def test_status_flags_from_bacpypes_bits() -> None:
    flags = StatusFlags([0, 1, 0, 0])
    assert normalize_status([flags[i] for i in range(4)]) == PointStatus.FAULT


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("degrees-celsius", "°C"),
        ("kilowatts", "kW"),
        ("pascals", "Pa"),
        ("percent", "%"),
        ("no-units", None),
        (None, None),
        ("unite-inconnue", "unite-inconnue"),
    ],
)
def test_unit_label(name: str | None, label: str | None) -> None:
    assert unit_label(name) == label


def test_writable_types() -> None:
    assert is_writable("analog-output") and is_writable("binary-value")
    assert is_writable("multi-state-value") and is_writable("multi-state-output")
    assert not is_writable("analog-input") and not is_writable("binary-input")


def test_build_reading_and_comm_lost() -> None:
    point_id = uuid.uuid4()
    reading = build_reading(point_id, BinaryPV("active"), [0, 0, 1, 0])
    assert (reading.value, reading.status) == (1.0, "overridden")
    assert reading.ts.tzinfo is not None  # UTC

    lost = comm_lost_reading(point_id)
    assert (lost.value, lost.status) == (None, "comm_lost")
