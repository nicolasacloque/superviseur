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
    """États d'une alarme (section 9.2). PENDING n'existe qu'en mémoire : rien n'est enregistré tant
    que la temporisation n'est pas écoulée ; NORMAL en base désigne une alarme close."""

    NORMAL = "normal"
    PENDING = "pending"
    ACTIVE_UNACKED = "active_unacked"
    ACTIVE_ACKED = "active_acked"
    CLEARED_UNACKED = "cleared_unacked"


class RoleName(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ENGINEER = "engineer"
    ADMIN = "admin"
