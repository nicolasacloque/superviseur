"""Évaluation des conditions d'alarme selon le type de règle (section 9.1)."""

from __future__ import annotations

import math

from app.common.models import AlarmKind

# Types dont la condition dépend de la valeur du point.
VALUE_KINDS = frozenset({AlarmKind.HIGH, AlarmKind.LOW, AlarmKind.STATE})
# Types qui exigent un seuil (secondes pour `stale`, valeur attendue pour `state`).
THRESHOLD_KINDS = frozenset({AlarmKind.HIGH, AlarmKind.LOW, AlarmKind.STATE, AlarmKind.STALE})


def evaluate_value(
    kind: str, value: float | None, threshold: float | None, hysteresis: float, previous: bool
) -> bool | None:
    """Condition d'une règle `high`, `low` ou `state` ; `None` = pas d'information.

    - `high` : vraie au-dessus du seuil, elle ne retombe qu'à `seuil - hystérésis`.
    - `low` : vraie sous le seuil, elle ne retombe qu'à `seuil + hystérésis`.
    - `state` : vraie quand la valeur vaut celle attendue (défaut d'un binaire : 1).

    Sans valeur (point en `comm_lost`), on ne peut ni déclencher ni effacer : `None`.
    """
    if value is None or threshold is None or math.isnan(value):
        return None
    if kind == AlarmKind.HIGH:
        return value > threshold or (previous and value > threshold - hysteresis)
    if kind == AlarmKind.LOW:
        return value < threshold or (previous and value < threshold + hysteresis)
    if kind == AlarmKind.STATE:
        return abs(value - threshold) < 1e-9
    return None


def is_stale(age_s: float, max_age_s: float) -> bool:
    """`stale` : aucune nouvelle valeur depuis plus de `max_age_s` secondes."""
    return age_s > max_age_s


def is_abnormal_event_state(to_state: str) -> bool:
    """`bacnet_event` : tout état d'événement autre que `normal` est une condition d'alarme."""
    return to_state.lower().replace("-", "").replace("_", "") != "normal"
