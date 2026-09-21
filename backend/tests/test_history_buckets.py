import pytest

from app.api.routes.points import auto_bucket_seconds, format_bucket


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (5, "5s"),
        (45, "45s"),
        (60, "1m"),
        (300, "5m"),
        (3600, "1h"),
        (7200, "2h"),
        (86400, "1d"),
        (172800, "2d"),
    ],
)
def test_format_bucket(seconds: int, expected: str) -> None:
    assert format_bucket(seconds) == expected


@pytest.mark.parametrize(
    ("span_s", "max_points", "expected"),
    [
        (300, 500, 0),  # 5 min : les données brutes tiennent largement
        (500, 500, 0),  # une tranche d'au plus 1 s : brut
        (3600, 500, 10),  # 1 h -> 7,2 s -> 10 s
        (86400, 500, 300),  # 24 h -> 173 s -> 5 min
        (7 * 86400, 500, 1800),  # 7 j -> 20 min -> 30 min
        (30 * 86400, 500, 10800),  # 30 j -> 86 min -> 3 h
        (365 * 86400, 500, 86400),  # 1 an -> 17 h -> 1 j
        (10 * 365 * 86400, 500, 86400 * 8),  # au-delà d'un jour : multiple de jours
    ],
)
def test_auto_bucket_picks_the_smallest_round_bucket(
    span_s: float, max_points: int, expected: int
) -> None:
    assert auto_bucket_seconds(span_s, max_points) == expected


def test_auto_bucket_never_exceeds_max_points() -> None:
    for span in (600, 3600, 86400, 7 * 86400, 30 * 86400, 200 * 86400):
        for max_points in (50, 500, 2000):
            size = auto_bucket_seconds(span, max_points)
            assert size == 0 or span / size <= max_points
