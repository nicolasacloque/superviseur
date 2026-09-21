"""Accès base de données du moteur d'alarmes (source de vérité : `alarm_rule`, `alarm_event`)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.models import AlarmState
from app.db.models import AlarmEvent, AlarmRule, Device, Point, PointLatest, Role, User


@dataclass
class RuleRecord:
    id: uuid.UUID
    point_id: uuid.UUID
    name: str | None
    kind: str
    threshold: float | None
    hysteresis: float
    delay_s: int
    severity: str
    notify: list[str]
    point_name: str
    path: str | None
    unit: str | None
    object_type: str
    object_instance: int
    device_id: uuid.UUID
    device_instance: int


@dataclass
class OpenEvent:
    id: uuid.UUID
    rule_id: uuid.UUID
    state: str
    raised_at: datetime
    acked_at: datetime | None = None
    acked_by: str | None = None
    cleared_at: datetime | None = None
    raised_value: float | None = None


@dataclass(frozen=True)
class LatestValue:
    ts: datetime
    value: float | None
    status: str


class AlarmStore(Protocol):
    async def load_rules(self) -> list[RuleRecord]: ...

    async def open_events(self) -> list[OpenEvent]: ...

    async def latest_values(
        self, point_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, LatestValue]: ...

    async def devices_online(self) -> dict[uuid.UUID, bool]: ...

    async def create_event(
        self, rule_id: uuid.UUID, raised_at: datetime, raised_value: float | None
    ) -> uuid.UUID: ...

    async def update_event(self, event_id: uuid.UUID, values: dict[str, Any]) -> None: ...

    async def user_login(self, user_id: uuid.UUID) -> str | None: ...

    async def user_role(self, user_id: uuid.UUID) -> str | None: ...


@dataclass
class MemoryAlarmStore:
    """Implémentation en mémoire : sert aux tests unitaires du moteur."""

    rules: list[RuleRecord] = field(default_factory=list)
    events: dict[uuid.UUID, OpenEvent] = field(default_factory=dict)
    latest: dict[uuid.UUID, LatestValue] = field(default_factory=dict)
    online: dict[uuid.UUID, bool] = field(default_factory=dict)
    logins: dict[uuid.UUID, str] = field(default_factory=dict)
    roles: dict[uuid.UUID, str] = field(default_factory=dict)
    history: list[tuple[str, uuid.UUID, dict[str, Any]]] = field(default_factory=list)
    fail: bool = False

    async def load_rules(self) -> list[RuleRecord]:
        return list(self.rules)

    async def open_events(self) -> list[OpenEvent]:
        return [e for e in self.events.values() if e.state != AlarmState.NORMAL]

    async def latest_values(self, point_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, LatestValue]:
        return {p: v for p, v in self.latest.items() if p in point_ids}

    async def devices_online(self) -> dict[uuid.UUID, bool]:
        return dict(self.online)

    async def create_event(
        self, rule_id: uuid.UUID, raised_at: datetime, raised_value: float | None
    ) -> uuid.UUID:
        if self.fail:
            raise OSError("base injoignable")
        event = OpenEvent(
            uuid.uuid4(),
            rule_id,
            AlarmState.ACTIVE_UNACKED.value,
            raised_at,
            raised_value=raised_value,
        )
        self.events[event.id] = event
        self.history.append(("create", event.id, {"state": event.state}))
        return event.id

    async def update_event(self, event_id: uuid.UUID, values: dict[str, Any]) -> None:
        if self.fail:
            raise OSError("base injoignable")
        event = self.events[event_id]
        for key, value in values.items():
            setattr(event, key, value)
        self.history.append(("update", event_id, dict(values)))

    async def user_login(self, user_id: uuid.UUID) -> str | None:
        return self.logins.get(user_id)

    async def user_role(self, user_id: uuid.UUID) -> str | None:
        return self.roles.get(user_id)


class DbAlarmStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def load_rules(self) -> list[RuleRecord]:
        query = (
            select(AlarmRule, Point, Device)
            .join(Point, AlarmRule.point_id == Point.id)
            .join(Device, Point.device_id == Device.id)
            .where(AlarmRule.enabled.is_(True))
        )
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        return [
            RuleRecord(
                id=rule.id,
                point_id=point.id,
                name=rule.name,
                kind=rule.kind,
                threshold=rule.threshold,
                hysteresis=rule.hysteresis,
                delay_s=rule.delay_s,
                severity=rule.severity,
                notify=list(rule.notify or []),
                point_name=point.name,
                path=point.path,
                unit=point.unit,
                object_type=point.object_type,
                object_instance=point.object_instance,
                device_id=device.id,
                device_instance=device.instance,
            )
            for rule, point, device in rows
        ]

    async def open_events(self) -> list[OpenEvent]:
        async with self._sessions() as session:
            events = (
                await session.scalars(
                    select(AlarmEvent).where(AlarmEvent.state != AlarmState.NORMAL.value)
                )
            ).all()
        return [
            OpenEvent(
                e.id,
                e.rule_id,
                e.state,
                e.raised_at,
                e.acked_at,
                e.acked_by,
                e.cleared_at,
                e.raised_value,
            )
            for e in events
        ]

    async def latest_values(self, point_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, LatestValue]:
        if not point_ids:
            return {}
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(PointLatest).where(PointLatest.point_id.in_(point_ids))
                )
            ).all()
        return {r.point_id: LatestValue(r.ts, r.value, r.status) for r in rows}

    async def devices_online(self) -> dict[uuid.UUID, bool]:
        async with self._sessions() as session:
            rows = (await session.execute(select(Device.id, Device.online))).all()
        return {device_id: online for device_id, online in rows}

    async def create_event(
        self, rule_id: uuid.UUID, raised_at: datetime, raised_value: float | None
    ) -> uuid.UUID:
        event = AlarmEvent(
            rule_id=rule_id,
            raised_at=raised_at,
            state=AlarmState.ACTIVE_UNACKED.value,
            raised_value=raised_value,
        )
        try:
            async with self._sessions() as session, session.begin():
                session.add(event)
                await session.flush()
                return event.id
        except IntegrityError:
            # Une alarme est déjà ouverte pour cette règle (index unique partiel) : on la reprend.
            async with self._sessions() as session:
                existing = await session.scalar(
                    select(AlarmEvent.id).where(
                        AlarmEvent.rule_id == rule_id, AlarmEvent.state != AlarmState.NORMAL.value
                    )
                )
            if existing is None:
                raise
            return existing

    async def update_event(self, event_id: uuid.UUID, values: dict[str, Any]) -> None:
        async with self._sessions() as session, session.begin():
            await session.execute(
                update(AlarmEvent).where(AlarmEvent.id == event_id).values(**values)
            )

    async def user_login(self, user_id: uuid.UUID) -> str | None:
        async with self._sessions() as session:
            login: str | None = await session.scalar(select(User.login).where(User.id == user_id))
        return login

    async def user_role(self, user_id: uuid.UUID) -> str | None:
        async with self._sessions() as session:
            role: str | None = await session.scalar(
                select(Role.name)
                .join(User, User.role_id == Role.id)
                .where(User.id == user_id, User.active.is_(True))
            )
        return role
