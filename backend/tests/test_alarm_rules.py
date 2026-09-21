"""Évaluation de la condition de chaque type de règle (section 9.1)."""

import pytest

from app.alarms.rules import evaluate_value, is_abnormal_event_state, is_stale


class TestHigh:
    def test_true_above_the_threshold_only(self) -> None:
        assert evaluate_value("high", 28.1, 28, 0, False) is True
        assert evaluate_value("high", 28.0, 28, 0, False) is False  # égal : pas au-dessus
        assert evaluate_value("high", 20, 28, 0, False) is False

    def test_hysteresis_keeps_the_alarm_until_the_value_falls_far_enough(self) -> None:
        # seuil 28, hystérésis 2 : déclenche au-dessus de 28, retombe à 26 ou moins
        assert evaluate_value("high", 27, 28, 2, previous=False) is False  # pas encore déclenché
        assert evaluate_value("high", 27, 28, 2, previous=True) is True  # maintenue
        assert evaluate_value("high", 26.5, 28, 2, previous=True) is True
        assert evaluate_value("high", 26, 28, 2, previous=True) is False  # retombée


class TestLow:
    def test_true_below_the_threshold_only(self) -> None:
        assert evaluate_value("low", 4.9, 5, 0, False) is True
        assert evaluate_value("low", 5.0, 5, 0, False) is False
        assert evaluate_value("low", 10, 5, 0, False) is False

    def test_hysteresis_mirrors_the_high_rule(self) -> None:
        assert evaluate_value("low", 6, 5, 2, previous=False) is False
        assert evaluate_value("low", 6, 5, 2, previous=True) is True
        assert evaluate_value("low", 7, 5, 2, previous=True) is False


class TestState:
    def test_true_when_the_value_equals_the_expected_state(self) -> None:
        assert evaluate_value("state", 1.0, 1, 0, False) is True
        assert evaluate_value("state", 0.0, 1, 0, False) is False
        assert evaluate_value("state", 3.0, 3, 0, False) is True  # multi-état

    def test_hysteresis_does_not_apply(self) -> None:
        assert evaluate_value("state", 0.0, 1, 5, previous=True) is False


class TestNoInformation:
    @pytest.mark.parametrize("kind", ["high", "low", "state"])
    def test_a_missing_value_neither_raises_nor_clears(self, kind: str) -> None:
        # point en comm_lost : la condition reste ce qu'elle était
        assert evaluate_value(kind, None, 10, 0, True) is None
        assert evaluate_value(kind, float("nan"), 10, 0, True) is None

    def test_missing_threshold_or_unsupported_kind(self) -> None:
        assert evaluate_value("high", 5, None, 0, False) is None
        assert evaluate_value("stale", 5, 10, 0, False) is None
        assert evaluate_value("comm_lost", 5, 10, 0, False) is None


def test_stale_is_true_only_beyond_the_maximum_age() -> None:
    assert is_stale(301, 300) is True
    assert is_stale(300, 300) is False
    assert is_stale(0, 300) is False


@pytest.mark.parametrize(
    ("state", "abnormal"),
    [
        ("normal", False),
        ("NORMAL", False),
        ("high-limit", True),
        ("low-limit", True),
        ("offnormal", True),
        ("fault", True),
        ("life-safety-alarm", True),
    ],
)
def test_bacnet_event_states(state: str, abnormal: bool) -> None:
    assert is_abnormal_event_state(state) is abnormal
