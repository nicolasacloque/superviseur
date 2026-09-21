"""Contenu des notifications d'alarme (email et webhook)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

SEVERITY_LABELS = {"info": "INFO", "warning": "AVERTISSEMENT", "critical": "CRITIQUE"}
KIND_LABELS = {
    "high": "Seuil haut",
    "low": "Seuil bas",
    "state": "État anormal",
    "stale": "Valeur figée",
    "comm_lost": "Communication perdue",
    "bacnet_event": "Événement BACnet",
}
VERBS = {
    "raised": "déclenchée",
    "cleared": "terminée (à acquitter)",
    "acked": "acquittée",
    "normal": "retour à la normale",
}


@dataclass
class AlarmInfo:
    """Photographie d'une alarme : sert aux messages, au WebSocket et à l'API."""

    event_id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: str | None
    kind: str
    severity: str
    state: str
    point_id: uuid.UUID
    point_name: str
    path: str | None
    unit: str | None
    threshold: float | None
    value: float | None
    raised_at: datetime
    acked_at: datetime | None = None
    acked_by: str | None = None
    cleared_at: datetime | None = None

    def title(self) -> str:
        return self.rule_name or KIND_LABELS.get(self.kind, self.kind)

    def as_dict(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return value.isoformat() if value else None

        return {
            "id": str(self.event_id),
            "rule_id": str(self.rule_id),
            "rule_name": self.rule_name,
            "kind": self.kind,
            "severity": self.severity,
            "state": self.state,
            "point_id": str(self.point_id),
            "point_name": self.point_name,
            "path": self.path,
            "unit": self.unit,
            "threshold": self.threshold,
            "value": self.value,
            "raised_at": iso(self.raised_at),
            "acked_at": iso(self.acked_at),
            "acked_by": self.acked_by,
            "cleared_at": iso(self.cleared_at),
        }


@dataclass(frozen=True)
class Notification:
    """Un changement d'état à notifier sur les canaux de la règle."""

    event_id: str
    kind: str
    severity: str
    subject: str
    body: str
    payload: dict[str, Any]
    channels: tuple[str, ...]


def _number(value: float | None, unit: str | None) -> str:
    if value is None:
        return "—"
    text = f"{value:g}"
    return f"{text} {unit}" if unit else text


def build_notification(info: AlarmInfo, kind: str, channels: list[str]) -> Notification:
    label = SEVERITY_LABELS.get(info.severity, info.severity.upper())
    where = info.path or info.point_name
    subject = f"[{label}] {info.title()} - {where} : {VERBS.get(kind, kind)}"
    lines = [
        subject,
        "",
        f"Point : {where}",
        f"Type : {KIND_LABELS.get(info.kind, info.kind)}",
        f"Sévérité : {label}",
        f"Valeur : {_number(info.value, info.unit)}",
    ]
    if info.threshold is not None and info.kind in ("high", "low", "state", "stale"):
        lines.append(
            f"Seuil : {_number(info.threshold, info.unit if info.kind != 'stale' else 's')}"
        )
    lines.append(f"Déclenchée le : {info.raised_at.isoformat(timespec='seconds')}")
    if info.cleared_at:
        lines.append(f"Terminée le : {info.cleared_at.isoformat(timespec='seconds')}")
    if info.acked_at:
        lines.append(
            f"Acquittée le : {info.acked_at.isoformat(timespec='seconds')} par {info.acked_by}"
        )
    return Notification(
        event_id=str(info.event_id),
        kind=kind,
        severity=info.severity,
        subject=subject,
        body="\n".join(lines),
        payload=info.as_dict(),
        channels=tuple(channels),
    )


def digest_text(
    notifications: list[Notification], window_s: float, limit: int = 50
) -> tuple[str, str]:
    """Sujet et corps d'un message regroupant plusieurs notifications."""
    subject = f"[Supervision] {len(notifications)} changements d'alarme en {window_s:g} s"
    lines = [subject, ""]
    lines += [f"- {n.subject}" for n in notifications[:limit]]
    if len(notifications) > limit:
        lines.append(f"... et {len(notifications) - limit} autres (voir la liste des alarmes)")
    return subject, "\n".join(lines)
