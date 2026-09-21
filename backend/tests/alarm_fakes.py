"""Doublures et fabriques pour tester le moteur d'alarmes sans base ni réseau."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.alarms.engine import AlarmEngine
from app.alarms.messages import Notification
from app.alarms.store import LatestValue, MemoryAlarmStore, RuleRecord

T0 = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


class FakeClocks:
    """Horloge monotone et horloge murale avancées ensemble."""

    def __init__(self) -> None:
        self.mono = 1000.0

    def advance(self, seconds: float) -> None:
        self.mono += seconds

    def monotonic(self) -> float:
        return self.mono

    def wall(self) -> datetime:
        return T0 + timedelta(seconds=self.mono - 1000.0)


class Recorder:
    """Publications Redis et notifications émises par le moteur."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[Notification] = []

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        self.published.append((channel, payload))

    async def submit(self, notification: Notification) -> None:
        self.notifications.append(notification)

    def transitions(self) -> list[str]:
        return [p["transition"] for _, p in self.published]

    def notified(self) -> list[str]:
        return [n.kind for n in self.notifications]


def make_rule(
    kind: str = "high",
    threshold: float | None = 28.0,
    *,
    hysteresis: float = 0.0,
    delay_s: int = 0,
    severity: str = "critical",
    notify: list[str] | None = None,
    point_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    name: str | None = "Règle de test",
) -> RuleRecord:
    return RuleRecord(
        id=uuid.uuid4(),
        point_id=point_id or uuid.uuid4(),
        name=name,
        kind=kind,
        threshold=threshold,
        hysteresis=hysteresis,
        delay_s=delay_s,
        severity=severity,
        notify=["email:ops@x.fr"] if notify is None else notify,
        point_name="Temp",
        path="Site/CTA-1/Temp",
        unit="°C",
        object_type="analog-input",
        object_instance=1,
        device_id=device_id or uuid.uuid4(),
        device_instance=1001,
    )


def build(
    *rules: RuleRecord, latest: dict[uuid.UUID, LatestValue] | None = None
) -> tuple[AlarmEngine, MemoryAlarmStore, Recorder, FakeClocks]:
    store = MemoryAlarmStore(rules=list(rules), latest=latest or {})
    recorder, clocks = Recorder(), FakeClocks()
    engine = AlarmEngine(
        store, recorder.publish, recorder, clock=clocks.monotonic, wall=clocks.wall
    )
    return engine, store, recorder, clocks
