"""Alarmes : liste, acquittement, règles (section 8.1 du cahier des charges)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import record_audit
from app.api.commands import send_and_wait
from app.api.deps import CurrentUser, RedisDep, SessionDep, SettingsDep, require_role
from app.api.schemas import (
    AckResponse,
    AlarmOut,
    AlarmPage,
    AlarmRuleIn,
    AlarmRuleOut,
    AlarmRuleUpdate,
)
from app.common.bus import CHANNEL_ALARM_RULES, STREAM_ALARM_CMD, alarm_result_channel
from app.common.models import AlarmState, RoleName
from app.db.models import AlarmEvent, AlarmRule, Point

log = logging.getLogger(__name__)

UNPROCESSABLE = 422  # nom du code HTTP variable selon les versions de Starlette

router = APIRouter(tags=["alarms"])

OPEN_STATES = (
    AlarmState.ACTIVE_UNACKED.value,
    AlarmState.ACTIVE_ACKED.value,
    AlarmState.CLEARED_UNACKED.value,
)
# Filtres nommés de `GET /alarms?state=` ; un nom d'état exact est aussi accepté.
STATE_GROUPS: dict[str, tuple[str, ...]] = {
    "open": OPEN_STATES,
    "active": (AlarmState.ACTIVE_UNACKED.value, AlarmState.ACTIVE_ACKED.value),
    "unacked": (AlarmState.ACTIVE_UNACKED.value, AlarmState.CLEARED_UNACKED.value),
    "closed": (AlarmState.NORMAL.value,),
}
ACKABLE = (AlarmState.ACTIVE_UNACKED.value, AlarmState.CLEARED_UNACKED.value)

Viewer = Depends(require_role(RoleName.VIEWER))
Operator = Annotated[CurrentUser, Depends(require_role(RoleName.OPERATOR))]
Engineer = Annotated[CurrentUser, Depends(require_role(RoleName.ENGINEER))]


def _alarm_query() -> Any:
    return (
        select(AlarmEvent, AlarmRule, Point)
        .join(AlarmRule, AlarmEvent.rule_id == AlarmRule.id)
        .join(Point, AlarmRule.point_id == Point.id)
    )


def _to_alarm(event: AlarmEvent, rule: AlarmRule, point: Point) -> AlarmOut:
    return AlarmOut(
        id=event.id,
        rule_id=rule.id,
        rule_name=rule.name,
        kind=rule.kind,
        severity=rule.severity,
        state=event.state,
        point_id=point.id,
        point_name=point.name,
        path=point.path,
        unit=point.unit,
        threshold=rule.threshold,
        value=event.raised_value,
        raised_at=event.raised_at,
        acked_at=event.acked_at,
        acked_by=event.acked_by,
        cleared_at=event.cleared_at,
    )


async def _load_alarm(session: AsyncSession, alarm_id: uuid.UUID) -> AlarmOut:
    row = (await session.execute(_alarm_query().where(AlarmEvent.id == alarm_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alarme introuvable")
    return _to_alarm(*row)


# -- alarmes -----------------------------------------------------------------------------------


@router.get("/alarms", response_model=AlarmPage, dependencies=[Viewer])
async def list_alarms(
    session: SessionDep,
    state: Annotated[
        str, Query(description="open (défaut), active, unacked, closed, all ou un état exact")
    ] = "open",
    severity: str | None = None,
    path: Annotated[str | None, Query(description="préfixe du chemin du point")] = None,
    point: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlarmPage:
    where: list[Any] = []
    if state in STATE_GROUPS:
        where.append(AlarmEvent.state.in_(STATE_GROUPS[state]))
    elif state != "all":
        if state not in {s.value for s in AlarmState}:
            raise HTTPException(UNPROCESSABLE, f"état inconnu : {state}")
        where.append(AlarmEvent.state == state)
    if severity:
        where.append(AlarmRule.severity == severity)
    if path:
        where.append(Point.path.startswith(path, autoescape=True))
    if point is not None:
        where.append(Point.id == point)
    total = await session.scalar(
        select(func.count())
        .select_from(AlarmEvent)
        .join(AlarmRule, AlarmEvent.rule_id == AlarmRule.id)
        .join(Point, AlarmRule.point_id == Point.id)
        .where(*where)
    )
    rows = (
        await session.execute(
            _alarm_query()
            .where(*where)
            .order_by(AlarmEvent.raised_at.desc(), AlarmEvent.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return AlarmPage(
        items=[_to_alarm(*row) for row in rows], total=total or 0, limit=limit, offset=offset
    )


@router.get("/alarms/{alarm_id}", response_model=AlarmOut, dependencies=[Viewer])
async def get_alarm(alarm_id: uuid.UUID, session: SessionDep) -> AlarmOut:
    return await _load_alarm(session, alarm_id)


@router.post("/alarms/{alarm_id}/ack", response_model=AckResponse)
async def acknowledge(
    alarm_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    user: Operator,
) -> AckResponse:
    """Acquitte une alarme : le moteur applique la transition de la machine à états."""
    alarm = await _load_alarm(session, alarm_id)

    async def refuse(code: int, detail: str) -> NoReturn:
        record_audit(
            session,
            request,
            user_id=user.id,
            action="alarm.ack",
            target=alarm.path or alarm.point_name,
            before={"state": alarm.state},
            after={"result": f"refusé: {detail}"},
        )
        await session.commit()
        raise HTTPException(code, detail)

    if alarm.state not in ACKABLE:
        await refuse(status.HTTP_409_CONFLICT, f"acquittement impossible dans l'état {alarm.state}")
    command_id = uuid.uuid4().hex
    result = await send_and_wait(
        redis,
        STREAM_ALARM_CMD,
        alarm_result_channel(command_id),
        {
            "command_id": command_id,
            "action": "ack",
            "event_id": str(alarm_id),
            "user_id": str(user.id),
            "issued_at": time.time(),
        },
        settings.write_timeout_s,
    )
    if result is None:
        await refuse(status.HTTP_504_GATEWAY_TIMEOUT, "le moteur d'alarmes n'a pas répondu")
    if result.get("status") != "ok":
        await refuse(status.HTTP_409_CONFLICT, result.get("message") or "acquittement refusé")

    updated = await _load_alarm(session, alarm_id)
    record_audit(
        session,
        request,
        user_id=user.id,
        action="alarm.ack",
        target=alarm.path or alarm.point_name,
        before={"state": alarm.state},
        after={"state": updated.state, "result": "ok"},
    )
    await session.commit()
    return AckResponse(status="ok", alarm=updated)


# -- règles ------------------------------------------------------------------------------------


def _rule_out(rule: AlarmRule, point: Point) -> AlarmRuleOut:
    return AlarmRuleOut(
        id=rule.id,
        point_id=point.id,
        point_name=point.name,
        path=point.path,
        name=rule.name,
        kind=rule.kind,
        threshold=rule.threshold,
        hysteresis=rule.hysteresis,
        delay_s=rule.delay_s,
        severity=rule.severity,
        notify=list(rule.notify or []),
        enabled=rule.enabled,
    )


def _rule_values(rule: AlarmRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "kind": rule.kind,
        "threshold": rule.threshold,
        "hysteresis": rule.hysteresis,
        "delay_s": rule.delay_s,
        "severity": rule.severity,
        "notify": list(rule.notify or []),
        "enabled": rule.enabled,
    }


async def _announce(redis: Any) -> None:
    """Prévient le moteur qu'il doit recharger les règles."""
    try:
        await redis.publish(CHANNEL_ALARM_RULES, json.dumps({"reload": True}))
    except Exception:  # le moteur les relit de toute façon chaque minute
        log.warning("notification du moteur d'alarmes impossible", exc_info=True)


async def _rule_and_point(session: AsyncSession, rule_id: uuid.UUID) -> tuple[AlarmRule, Point]:
    row = (
        await session.execute(
            select(AlarmRule, Point)
            .join(Point, AlarmRule.point_id == Point.id)
            .where(AlarmRule.id == rule_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "règle introuvable")
    return row[0], row[1]


@router.get("/alarm-rules", response_model=list[AlarmRuleOut])
async def list_rules(
    session: SessionDep,
    _user: Engineer,
    point: uuid.UUID | None = None,
    enabled: bool | None = None,
) -> list[AlarmRuleOut]:
    query = select(AlarmRule, Point).join(Point, AlarmRule.point_id == Point.id)
    if point is not None:
        query = query.where(AlarmRule.point_id == point)
    if enabled is not None:
        query = query.where(AlarmRule.enabled.is_(enabled))
    rows = (await session.execute(query.order_by(Point.path, AlarmRule.kind))).all()
    return [_rule_out(rule, pt) for rule, pt in rows]


@router.get("/alarm-rules/{rule_id}", response_model=AlarmRuleOut)
async def get_rule(rule_id: uuid.UUID, session: SessionDep, _user: Engineer) -> AlarmRuleOut:
    return _rule_out(*await _rule_and_point(session, rule_id))


@router.post("/alarm-rules", response_model=AlarmRuleOut, status_code=status.HTTP_201_CREATED)
async def create_rule(
    body: AlarmRuleIn,
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    user: Engineer,
) -> AlarmRuleOut:
    point = await session.get(Point, body.point_id)
    if point is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "point introuvable")
    rule = AlarmRule(
        point_id=body.point_id,
        name=body.name,
        kind=body.kind.value,
        threshold=body.threshold,
        hysteresis=body.hysteresis,
        delay_s=body.delay_s,
        severity=body.severity.value,
        notify=body.notify,
        enabled=body.enabled,
    )
    session.add(rule)
    await session.flush()
    record_audit(
        session,
        request,
        user_id=user.id,
        action="alarm_rule.create",
        target=point.path or str(point.id),
        after=_rule_values(rule),
    )
    await session.commit()
    await _announce(redis)
    return _rule_out(rule, point)


@router.patch("/alarm-rules/{rule_id}", response_model=AlarmRuleOut)
async def update_rule(
    rule_id: uuid.UUID,
    body: AlarmRuleUpdate,
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    user: Engineer,
) -> AlarmRuleOut:
    rule, point = await _rule_and_point(session, rule_id)
    before = _rule_values(rule)
    merged = {**before, **body.model_dump(exclude_unset=True, mode="json")}
    try:  # la règle résultante doit rester cohérente (seuil obligatoire, canaux valides...)
        checked = AlarmRuleIn.model_validate({**merged, "point_id": rule.point_id})
    except ValidationError as exc:
        raise HTTPException(
            UNPROCESSABLE, exc.errors(include_url=False, include_context=False, include_input=False)
        ) from exc
    rule.name, rule.kind, rule.threshold = checked.name, checked.kind.value, checked.threshold
    rule.hysteresis, rule.delay_s = checked.hysteresis, checked.delay_s
    rule.severity, rule.notify, rule.enabled = (
        checked.severity.value,
        checked.notify,
        checked.enabled,
    )
    after = _rule_values(rule)
    if after != before:
        record_audit(
            session,
            request,
            user_id=user.id,
            action="alarm_rule.update",
            target=point.path or str(point.id),
            before={k: v for k, v in before.items() if after[k] != v},
            after={k: v for k, v in after.items() if before[k] != v},
        )
        await session.commit()
        await _announce(redis)
    return _rule_out(rule, point)


@router.delete("/alarm-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    rule_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    user: Engineer,
) -> Response:
    rule, point = await _rule_and_point(session, rule_id)
    opened = await session.scalar(
        select(func.count())
        .select_from(AlarmEvent)
        .where(AlarmEvent.rule_id == rule_id, AlarmEvent.state != AlarmState.NORMAL.value)
    )
    if opened:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "une alarme est ouverte : acquittez-la ou désactivez la règle plutôt que la supprimer",
        )
    record_audit(
        session,
        request,
        user_id=user.id,
        action="alarm_rule.delete",
        target=point.path or str(point.id),
        before=_rule_values(rule),
    )
    await session.delete(rule)
    await session.commit()
    await _announce(redis)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
