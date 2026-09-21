"""Machine à états des alarmes : chaque transition du diagramme 9.2 et rien d'autre."""

import pytest

from app.alarms.machine import AlarmMachine, Transition, TransitionKind
from app.common.models import AlarmState as S

K = TransitionKind


def kinds(transitions: list[Transition]) -> list[TransitionKind]:
    return [t.kind for t in transitions]


def machine(delay: float = 0, state: S = S.NORMAL) -> AlarmMachine:
    return AlarmMachine(delay, state)


class TestDiagramTransitions:
    def test_normal_to_pending_when_the_condition_becomes_true(self) -> None:
        m = machine(delay=10)
        assert kinds(m.update(True, 0)) == [K.PENDING]
        assert m.state is S.PENDING

    def test_pending_back_to_normal_if_the_condition_disappears_before_the_delay(self) -> None:
        m = machine(delay=10)
        m.update(True, 0)
        assert kinds(m.update(False, 5)) == [K.CANCELLED]
        assert m.state is S.NORMAL

    def test_pending_to_active_unacked_when_the_delay_has_elapsed(self) -> None:
        m = machine(delay=10)
        m.update(True, 0)
        assert m.update(True, 9.9) == []  # pas encore
        assert kinds(m.update(True, 10)) == [K.RAISED]
        assert m.state is S.ACTIVE_UNACKED

    def test_active_unacked_to_active_acked_on_acknowledgement(self) -> None:
        m = machine(state=S.ACTIVE_UNACKED)
        transition = m.acknowledge()
        assert transition == Transition(S.ACTIVE_UNACKED, S.ACTIVE_ACKED, K.ACKED)
        assert m.state is S.ACTIVE_ACKED

    def test_active_unacked_to_cleared_unacked_when_the_condition_disappears(self) -> None:
        m = machine(state=S.ACTIVE_UNACKED)
        assert kinds(m.update(False, 0)) == [K.CLEARED]
        assert m.state is S.CLEARED_UNACKED

    def test_active_acked_to_normal_when_the_condition_disappears(self) -> None:
        m = machine(state=S.ACTIVE_ACKED)
        assert kinds(m.update(False, 0)) == [K.NORMAL]
        assert m.state is S.NORMAL

    def test_cleared_unacked_to_normal_on_acknowledgement(self) -> None:
        m = machine(state=S.CLEARED_UNACKED)
        assert m.acknowledge() == Transition(S.CLEARED_UNACKED, S.NORMAL, K.ACKED)
        assert m.state is S.NORMAL

    def test_cleared_unacked_back_to_active_unacked_when_the_condition_returns(self) -> None:
        m = machine(state=S.CLEARED_UNACKED)
        assert kinds(m.update(True, 0)) == [K.RAISED]
        assert m.state is S.ACTIVE_UNACKED


class TestNoDelay:
    def test_a_zero_delay_raises_immediately_through_pending(self) -> None:
        m = machine(delay=0)
        result = m.update(True, 100)
        assert kinds(result) == [K.PENDING, K.RAISED]
        assert [t.persistent for t in result] == [False, True]
        assert m.state is S.ACTIVE_UNACKED


class TestIdempotence:
    """Une seule notification par changement d'état : rappeler ne change rien."""

    @pytest.mark.parametrize("state", list(S))
    def test_repeating_the_same_condition_produces_no_further_transition(self, state: S) -> None:
        m = machine(delay=0, state=state)
        condition = state in (S.ACTIVE_UNACKED, S.ACTIVE_ACKED, S.PENDING)
        m.update(condition, 0)
        settled = m.state
        for now in range(1, 6):
            assert m.update(condition, now) == []
        assert m.state is settled

    def test_a_long_active_alarm_stays_silent(self) -> None:
        m = machine(delay=0)
        assert len([t for t in m.update(True, 0) if t.persistent]) == 1
        assert all(m.update(True, n) == [] for n in range(1, 100))


class TestAcknowledgeRestrictions:
    @pytest.mark.parametrize("state", [S.NORMAL, S.PENDING, S.ACTIVE_ACKED])
    def test_nothing_to_acknowledge(self, state: S) -> None:
        m = machine(state=state)
        assert m.acknowledge() is None
        assert m.state is (S.NORMAL if state is S.PENDING else state)

    def test_an_acknowledged_alarm_cannot_be_acknowledged_twice(self) -> None:
        m = machine(state=S.ACTIVE_UNACKED)
        assert m.acknowledge() is not None
        assert m.acknowledge() is None


class TestFullLifecycles:
    def test_raise_ack_clear(self) -> None:
        m = machine(delay=5)
        seen = kinds(m.update(True, 0))
        seen += kinds(m.update(True, 5))
        assert m.acknowledge() is not None
        seen += kinds(m.update(False, 8))
        assert seen == [K.PENDING, K.RAISED, K.NORMAL]
        assert m.state is S.NORMAL

    def test_raise_clear_ack(self) -> None:
        m = machine(delay=0)
        seen = kinds(m.update(True, 0))
        seen += kinds(m.update(False, 1))
        transition = m.acknowledge()
        assert transition is not None
        assert seen == [K.PENDING, K.RAISED, K.CLEARED]
        assert m.state is S.NORMAL

    def test_flapping_condition_never_leaves_two_open_states(self) -> None:
        m = machine(delay=0)
        m.update(True, 0)  # active
        m.update(False, 1)  # cleared, non acquittée
        again = m.update(True, 2)  # revient : la même alarme redevient active
        assert kinds(again) == [K.RAISED] and m.state is S.ACTIVE_UNACKED

    def test_delay_restarts_after_a_cancelled_pending(self) -> None:
        m = machine(delay=10)
        m.update(True, 0)
        m.update(False, 6)  # annulé
        m.update(True, 8)  # nouvelle attente qui repart de 8
        assert m.update(True, 15) == []
        assert kinds(m.update(True, 18)) == [K.RAISED]


def test_pending_cannot_be_restored_after_a_restart() -> None:
    assert machine(state=S.PENDING).state is S.NORMAL
