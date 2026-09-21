"""Machine à états d'une alarme (section 9.2 du cahier des charges).

    Normal --condition vraie--> Pending --delay_s écoulé--> ActiveUnacked
    Pending --condition fausse avant delay_s--> Normal
    ActiveUnacked --acquittement--> ActiveAcked --condition disparue--> Normal
    ActiveUnacked --condition disparue--> ClearedUnacked --acquittement--> Normal
    ClearedUnacked --condition revient--> ActiveUnacked

Module pur : le temps est fourni par l'appelant, rien n'est lu ni écrit ailleurs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.common.models import AlarmState


class TransitionKind(StrEnum):
    # Internes : rien n'est enregistré ni notifié.
    PENDING = "pending"
    CANCELLED = "cancelled"
    # Persistantes : une ligne `alarm_event` est mise à jour, une notification part.
    RAISED = "raised"
    CLEARED = "cleared"
    ACKED = "acked"
    NORMAL = "normal"


PERSISTENT = frozenset(
    {TransitionKind.RAISED, TransitionKind.CLEARED, TransitionKind.ACKED, TransitionKind.NORMAL}
)


@dataclass(frozen=True)
class Transition:
    from_state: AlarmState
    to_state: AlarmState
    kind: TransitionKind

    @property
    def persistent(self) -> bool:
        return self.kind in PERSISTENT


class AlarmMachine:
    """Une instance par règle. `update` est idempotent : rappeler avec la même condition ne
    produit aucune transition, ce qui garantit une seule notification par changement d'état."""

    def __init__(self, delay_s: float = 0.0, state: AlarmState = AlarmState.NORMAL) -> None:
        self.delay_s = delay_s
        # PENDING n'est pas restaurable : après un redémarrage la temporisation repart de zéro.
        self.state: AlarmState = AlarmState.NORMAL if state is AlarmState.PENDING else state
        self.pending_since: float | None = None

    def update(self, condition: bool, now: float) -> list[Transition]:
        """Applique la condition courante à l'instant `now` (secondes, horloge monotone)."""
        out: list[Transition] = []
        if self.state is AlarmState.NORMAL and condition:
            self.state = AlarmState.PENDING
            self.pending_since = now
            out.append(Transition(AlarmState.NORMAL, AlarmState.PENDING, TransitionKind.PENDING))

        if self.state is AlarmState.PENDING:
            if not condition:
                self.state, self.pending_since = AlarmState.NORMAL, None
                out.append(
                    Transition(AlarmState.PENDING, AlarmState.NORMAL, TransitionKind.CANCELLED)
                )
            elif (
                now - (self.pending_since if self.pending_since is not None else now)
                >= self.delay_s
            ):
                self.state, self.pending_since = AlarmState.ACTIVE_UNACKED, None
                out.append(
                    Transition(AlarmState.PENDING, AlarmState.ACTIVE_UNACKED, TransitionKind.RAISED)
                )
        elif self.state is AlarmState.ACTIVE_UNACKED and not condition:
            self.state = AlarmState.CLEARED_UNACKED
            out.append(
                Transition(
                    AlarmState.ACTIVE_UNACKED, AlarmState.CLEARED_UNACKED, TransitionKind.CLEARED
                )
            )
        elif self.state is AlarmState.ACTIVE_ACKED and not condition:
            self.state = AlarmState.NORMAL
            out.append(
                Transition(AlarmState.ACTIVE_ACKED, AlarmState.NORMAL, TransitionKind.NORMAL)
            )
        elif self.state is AlarmState.CLEARED_UNACKED and condition:
            self.state = AlarmState.ACTIVE_UNACKED
            out.append(
                Transition(
                    AlarmState.CLEARED_UNACKED, AlarmState.ACTIVE_UNACKED, TransitionKind.RAISED
                )
            )
        return out

    def acknowledge(self) -> Transition | None:
        """Acquittement opérateur ; `None` si l'état ne s'y prête pas (rien ne change)."""
        if self.state is AlarmState.ACTIVE_UNACKED:
            self.state = AlarmState.ACTIVE_ACKED
            return Transition(
                AlarmState.ACTIVE_UNACKED, AlarmState.ACTIVE_ACKED, TransitionKind.ACKED
            )
        if self.state is AlarmState.CLEARED_UNACKED:
            self.state = AlarmState.NORMAL
            return Transition(AlarmState.CLEARED_UNACKED, AlarmState.NORMAL, TransitionKind.ACKED)
        return None
