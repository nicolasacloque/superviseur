"""Moteur d'alarmes : évalue les règles, applique la machine à états, enregistre et notifie.

Le moteur est le seul à écrire `alarm_event`. Un changement d'état n'est notifié qu'une fois : la
machine ne produit une transition que lorsque l'état change réellement.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.alarms.machine import AlarmMachine, Transition, TransitionKind
from app.alarms.messages import AlarmInfo, Notification, build_notification
from app.alarms.rules import VALUE_KINDS, evaluate_value, is_abnormal_event_state, is_stale
from app.alarms.store import AlarmStore, OpenEvent, RuleRecord
from app.common.bus import CHANNEL_ALARM_EVENT
from app.common.models import AlarmKind, AlarmState

log = logging.getLogger(__name__)

Publish = Callable[[str, dict[str, Any]], Awaitable[None]]


class NotifierLike(Protocol):
    async def submit(self, notification: Notification) -> None: ...


@dataclass
class AckResult:
    ok: bool
    error: str | None = None


@dataclass
class RuleRuntime:
    rec: RuleRecord
    machine: AlarmMachine
    condition: bool = False
    event_id: uuid.UUID | None = None
    raised_at: datetime | None = None
    acked_at: datetime | None = None
    acked_by: str | None = None
    cleared_at: datetime | None = None
    value: float | None = None
    raised_value: float | None = None
    # Dernière valeur reçue, en secondes de l'horloge monotone (règle `stale`).
    last_valid: float = 0.0


class AlarmEngine:
    def __init__(
        self,
        store: AlarmStore,
        publish: Publish,
        notifier: NotifierLike,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._publish = publish
        self._notifier = notifier
        self._clock = clock
        self._wall = wall
        self._lock = asyncio.Lock()
        self._rules: dict[uuid.UUID, RuleRuntime] = {}
        self._by_point: dict[uuid.UUID, list[RuleRuntime]] = {}
        self._by_device: dict[uuid.UUID, list[RuleRuntime]] = {}
        self._by_object: dict[tuple[int, str, int], list[RuleRuntime]] = {}

    # -- chargement --------------------------------------------------------------------------

    def states(self) -> dict[uuid.UUID, AlarmState]:
        return {rule_id: rt.machine.state for rule_id, rt in self._rules.items()}

    async def load(self) -> None:
        """(Re)charge les règles actives ; l'état des règles déjà connues est conservé."""
        async with self._lock:
            records = await self._store.load_rules()
            events = {e.rule_id: e for e in await self._store.open_events()}
            fresh: list[RuleRuntime] = []
            rules: dict[uuid.UUID, RuleRuntime] = {}
            for rec in records:
                runtime = self._rules.get(rec.id)
                if runtime is not None:
                    runtime.rec = rec
                    runtime.machine.delay_s = rec.delay_s
                else:
                    runtime = self._restore(rec, events.get(rec.id))
                    fresh.append(runtime)
                rules[rec.id] = runtime
            # Règle désactivée avec une alarme ouverte : l'alarme n'a plus d'évaluateur, on la clôt.
            for rule_id, event in events.items():
                if rule_id not in rules:
                    await self._close_orphan(event)
            self._rules = rules
            self._index()
            await self._initial_evaluation(fresh)

    def _restore(self, rec: RuleRecord, event: OpenEvent | None) -> RuleRuntime:
        state = AlarmState(event.state) if event else AlarmState.NORMAL
        runtime = RuleRuntime(rec, AlarmMachine(rec.delay_s, state), last_valid=self._clock())
        if event is not None:
            runtime.event_id = event.id
            runtime.raised_at, runtime.acked_at = event.raised_at, event.acked_at
            runtime.acked_by, runtime.cleared_at = event.acked_by, event.cleared_at
            runtime.raised_value = runtime.value = event.raised_value
            runtime.condition = state in (AlarmState.ACTIVE_UNACKED, AlarmState.ACTIVE_ACKED)
        return runtime

    def _index(self) -> None:
        self._by_point, self._by_device, self._by_object = {}, {}, {}
        for runtime in self._rules.values():
            rec = runtime.rec
            self._by_point.setdefault(rec.point_id, []).append(runtime)
            if rec.kind == AlarmKind.COMM_LOST:
                self._by_device.setdefault(rec.device_id, []).append(runtime)
            if rec.kind == AlarmKind.BACNET_EVENT:
                key = (rec.device_instance, rec.object_type, rec.object_instance)
                self._by_object.setdefault(key, []).append(runtime)

    async def _close_orphan(self, event: OpenEvent) -> None:
        now = self._wall()
        await self._store.update_event(
            event.id, {"state": AlarmState.NORMAL.value, "cleared_at": event.cleared_at or now}
        )
        await self._publish(
            CHANNEL_ALARM_EVENT,
            {
                "transition": TransitionKind.NORMAL.value,
                "event": {"id": str(event.id), "state": "normal"},
            },
        )

    async def _initial_evaluation(self, fresh: list[RuleRuntime]) -> None:
        """Rattrape l'état courant : valeurs déjà en base, devices hors ligne."""
        if not fresh:
            return
        latest = await self._store.latest_values([r.rec.point_id for r in fresh])
        online = await self._store.devices_online()
        now_wall = self._wall()
        for runtime in fresh:
            rec = runtime.rec
            last = latest.get(rec.point_id)
            if last is not None:
                runtime.last_valid = self._clock() - max(0.0, (now_wall - last.ts).total_seconds())
            if rec.kind in VALUE_KINDS and last is not None:
                condition = evaluate_value(
                    rec.kind, last.value, rec.threshold, rec.hysteresis, runtime.condition
                )
                if condition is not None:
                    await self._update(runtime, condition, last.value)
            elif rec.kind == AlarmKind.COMM_LOST and online.get(rec.device_id) is False:
                await self._update(runtime, True)

    # -- observations ------------------------------------------------------------------------

    async def on_value(self, point_id: uuid.UUID, value: float | None, status: str = "ok") -> None:
        async with self._lock:
            for runtime in self._by_point.get(point_id, []):
                rec = runtime.rec
                if value is not None:
                    runtime.last_valid = self._clock()
                if rec.kind in VALUE_KINDS:
                    condition = evaluate_value(
                        rec.kind, value, rec.threshold, rec.hysteresis, runtime.condition
                    )
                    if condition is not None:
                        await self._update(runtime, condition, value)
                elif rec.kind == AlarmKind.STALE and value is not None:
                    await self._update(runtime, False, value)  # une valeur fraîche efface

    async def on_device_status(self, device_id: uuid.UUID, online: bool) -> None:
        async with self._lock:
            for runtime in self._by_device.get(device_id, []):
                await self._update(runtime, not online)

    async def on_bacnet_event(
        self, device_instance: int, object_type: str, object_instance: int, to_state: str
    ) -> None:
        async with self._lock:
            key = (device_instance, object_type, object_instance)
            for runtime in self._by_object.get(key, []):
                await self._update(runtime, is_abnormal_event_state(to_state))

    async def tick(self) -> None:
        """À appeler chaque seconde : temporisations écoulées et règles `stale`."""
        async with self._lock:
            now = self._clock()
            for runtime in list(self._rules.values()):
                rec = runtime.rec
                if rec.kind == AlarmKind.STALE and rec.threshold is not None:
                    await self._update(runtime, is_stale(now - runtime.last_valid, rec.threshold))
                elif runtime.machine.state is AlarmState.PENDING:
                    await self._update(runtime, runtime.condition)

    async def acknowledge(self, event_id: uuid.UUID, user_id: uuid.UUID) -> AckResult:
        async with self._lock:
            runtime = next((r for r in self._rules.values() if r.event_id == event_id), None)
            if runtime is None:
                return AckResult(False, "alarme introuvable ou déjà close")
            transition = runtime.machine.acknowledge()
            if transition is None:
                return AckResult(
                    False, f"acquittement impossible dans l'état {runtime.machine.state.value}"
                )
            try:
                login = await self._store.user_login(user_id)
                await self._persist(runtime, transition, acked_by=login or str(user_id))
            except Exception:
                log.exception("acquittement non enregistré")
                runtime.machine.state = (
                    transition.from_state
                )  # rien n'a changé : on pourra réessayer
                return AckResult(False, "erreur d'enregistrement de l'acquittement")
            return AckResult(True)

    # -- transitions -------------------------------------------------------------------------

    async def _update(
        self, runtime: RuleRuntime, condition: bool, value: float | None = None
    ) -> None:
        runtime.condition = condition
        if value is not None:
            runtime.value = value
        for transition in runtime.machine.update(condition, self._clock()):
            if not transition.persistent:
                continue
            try:
                await self._persist(runtime, transition)
            except Exception:
                # Base injoignable : on annule la transition pour la retenter à la prochaine
                # observation, sans notifier ni publier quoi que ce soit entre-temps.
                log.exception(
                    "règle %s : transition %s non enregistrée", runtime.rec.id, transition.kind
                )
                runtime.machine.state = transition.from_state
                return

    async def _persist(
        self, runtime: RuleRuntime, transition: Transition, acked_by: str | None = None
    ) -> None:
        now = self._wall()
        kind = transition.kind
        state = transition.to_state.value
        if kind is TransitionKind.RAISED:
            if runtime.event_id is None:
                runtime.event_id = await self._store.create_event(
                    runtime.rec.id, now, runtime.value
                )
                runtime.raised_at, runtime.raised_value = now, runtime.value
                runtime.acked_at = runtime.acked_by = None
            else:  # la condition est revenue avant l'acquittement : même alarme, de nouveau active
                await self._store.update_event(
                    runtime.event_id, {"state": state, "cleared_at": None}
                )
            runtime.cleared_at = None
        elif runtime.event_id is None:
            raise RuntimeError("transition sans alarme ouverte")
        elif kind is TransitionKind.CLEARED:
            runtime.cleared_at = now
            await self._store.update_event(runtime.event_id, {"state": state, "cleared_at": now})
        elif kind is TransitionKind.ACKED:
            values: dict[str, Any] = {"state": state, "acked_at": now, "acked_by": acked_by}
            await self._store.update_event(runtime.event_id, values)
            runtime.acked_at, runtime.acked_by = now, acked_by
        elif kind is TransitionKind.NORMAL:
            runtime.cleared_at = now
            await self._store.update_event(runtime.event_id, {"state": state, "cleared_at": now})

        info = self._info(runtime, transition.to_state)
        await self._publish(
            CHANNEL_ALARM_EVENT, {"transition": kind.value, "event": info.as_dict()}
        )
        if runtime.rec.notify:
            await self._notifier.submit(build_notification(info, kind.value, runtime.rec.notify))
        if transition.to_state is AlarmState.NORMAL:
            runtime.event_id = None

    def _info(self, runtime: RuleRuntime, state: AlarmState) -> AlarmInfo:
        rec = runtime.rec
        assert runtime.event_id is not None and runtime.raised_at is not None
        return AlarmInfo(
            event_id=runtime.event_id,
            rule_id=rec.id,
            rule_name=rec.name,
            kind=rec.kind,
            severity=rec.severity,
            state=state.value,
            point_id=rec.point_id,
            point_name=rec.point_name,
            path=rec.path,
            unit=rec.unit,
            threshold=rec.threshold,
            value=runtime.raised_value if runtime.raised_value is not None else runtime.value,
            raised_at=runtime.raised_at,
            acked_at=runtime.acked_at,
            acked_by=runtime.acked_by,
            cleared_at=runtime.cleared_at,
        )
