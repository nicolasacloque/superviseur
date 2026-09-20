"""Énumérations partagées entre les services (valeurs stockées en base sous forme de texte)."""

from __future__ import annotations

from enum import StrEnum


class PointStatus(StrEnum):
    OK = "ok"
    FAULT = "fault"
    OVERRIDDEN = "overridden"
    OUT_OF_SERVICE = "out_of_service"
    STALE = "stale"
    COMM_LOST = "comm_lost"


class AlarmKind(StrEnum):
    HIGH = "high"
    LOW = "low"
    STATE = "state"
    STALE = "stale"
    COMM_LOST = "comm_lost"
    BACNET_EVENT = "bacnet_event"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlarmState(StrEnum):
    """États persistants d'une alarme (Pending reste en mémoire ; Normal = alarme close)."""

    ACTIVE_UNACKED = "active_unacked"
    ACTIVE_ACKED = "active_acked"
    CLEARED_UNACKED = "cleared_unacked"
    NORMAL = "normal"


class RoleName(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ENGINEER = "engineer"
    ADMIN = "admin"
