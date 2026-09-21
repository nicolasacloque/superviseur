"""Points : recherche paginée, fiche avec dernière valeur, historique."""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import ColumnElement, delete, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import record_audit
from app.api.deps import CurrentUser, RedisDep, SessionDep, require_role
from app.api.schemas import (
    HistoryItem,
    HistoryOut,
    LatestOut,
    PointDetail,
    PointOut,
    PointPage,
    PointUpdate,
)
from app.common.bus import CHANNEL_POINT_CONFIG
from app.common.models import RoleName
from app.db.models import Point, PointLatest, PointTag, Sample

UNPROCESSABLE = 422  # nom du code HTTP variable selon les versions de Starlette

log = logging.getLogger(__name__)

router = APIRouter(tags=["points"], dependencies=[Depends(require_role(RoleName.VIEWER))])

MAX_PAGE = 500
MAX_HISTORY_ROWS = 20_000
# Tranches proposées par `bucket=auto` (secondes) : la plus petite qui tient dans `max_points`.
_NICE_BUCKETS = (5, 10, 30, 60, 300, 900, 1800, 3600, 10800, 21600, 43200, 86400)
_BUCKET = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _filters(
    path: str | None,
    tag: str | None,
    device: uuid.UUID | None,
    q: str | None,
    writable: bool | None,
    include_missing: bool,
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if not include_missing:
        conditions.append(Point.missing.is_(False))
    if path:
        conditions.append(Point.path.startswith(path, autoescape=True))
    if device is not None:
        conditions.append(Point.device_id == device)
    if q:
        conditions.append(
            Point.name.icontains(q, autoescape=True)
            | Point.description.icontains(q, autoescape=True)
            | Point.path.icontains(q, autoescape=True)
        )
    if tag:
        conditions.append(exists().where(PointTag.point_id == Point.id, PointTag.tag == tag))
    if writable is not None:
        conditions.append(Point.writable.is_(writable))
    return conditions


async def _decorate(session: AsyncSession, outs: list[PointOut]) -> None:
    """Ajoute tags et dernière valeur en deux requêtes pour toute la page."""
    if not outs:
        return
    ids = [o.id for o in outs]
    tags: dict[uuid.UUID, list[str]] = {}
    for point_id, tag in await session.execute(
        select(PointTag.point_id, PointTag.tag)
        .where(PointTag.point_id.in_(ids))
        .order_by(PointTag.tag)
    ):
        tags.setdefault(point_id, []).append(tag)
    latest = {
        row.point_id: row
        for row in (
            await session.scalars(select(PointLatest).where(PointLatest.point_id.in_(ids)))
        ).all()
    }
    for out in outs:
        out.tags = tags.get(out.id, [])
        if (row := latest.get(out.id)) is not None:
            out.latest = LatestOut(ts=row.ts, value=row.value, status=row.status)


@router.get("/points", response_model=PointPage)
async def list_points(
    session: SessionDep,
    path: Annotated[str | None, Query(description="préfixe du chemin logique")] = None,
    tag: str | None = None,
    device: uuid.UUID | None = None,
    q: Annotated[str | None, Query(description="recherche dans nom, description, chemin")] = None,
    writable: bool | None = None,
    include_missing: bool = False,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PointPage:
    where = _filters(path, tag, device, q, writable, include_missing)
    total = await session.scalar(select(func.count()).select_from(Point).where(*where))
    points = (
        await session.scalars(
            select(Point).where(*where).order_by(Point.path, Point.name).limit(limit).offset(offset)
        )
    ).all()
    outs = [PointOut.model_validate(p) for p in points]
    await _decorate(session, outs)
    return PointPage(items=outs, total=total or 0, limit=limit, offset=offset)


@router.get("/points/{point_id}", response_model=PointDetail)
async def get_point(point_id: uuid.UUID, session: SessionDep) -> PointDetail:
    point = await session.get(Point, point_id)
    if point is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "point introuvable")
    detail = PointDetail.model_validate(point)
    await _decorate(session, [detail])
    return detail


def _parse_bucket(bucket: str) -> int:
    match = _BUCKET.match(bucket)
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)] if match else 0
    if seconds <= 0:
        raise HTTPException(UNPROCESSABLE, "bucket invalide (exemples : 30s, 5m, 1h, 1d, ou auto)")
    return seconds


def format_bucket(seconds: int) -> str:
    """5 -> `5s`, 300 -> `5m`, 3600 -> `1h`, 86400 -> `1d`."""
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def auto_bucket_seconds(span_s: float, max_points: int) -> int:
    """Plus petite tranche « ronde » donnant au plus `max_points` tranches ; 0 = données brutes."""
    wanted = span_s / max_points
    if wanted <= 1:
        return 0
    for size in _NICE_BUCKETS:
        if size >= wanted:
            return size
    return math.ceil(wanted / 86400) * 86400


@router.get("/points/{point_id}/history", response_model=HistoryOut)
async def point_history(
    point_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    bucket: Annotated[
        str | None,
        Query(description="ex. 5m, 1h : moyenne/min/max par tranche ; `auto` : selon `max_points`"),
    ] = None,
    max_points: Annotated[
        int, Query(ge=10, le=5000, description="nombre de tranches visé avec bucket=auto")
    ] = 500,
) -> HistoryOut:
    if await session.get(Point, point_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "point introuvable")
    end = to or datetime.now(UTC)
    start = from_ or end - timedelta(hours=24)
    if start >= end:
        raise HTTPException(UNPROCESSABLE, "`from` doit précéder `to`")

    seconds = 0
    if bucket == "auto":
        seconds = auto_bucket_seconds((end - start).total_seconds(), max_points)
    elif bucket is not None:
        seconds = _parse_bucket(bucket)

    if seconds == 0:
        rows = (
            await session.scalars(
                select(Sample)
                .where(Sample.point_id == point_id, Sample.ts >= start, Sample.ts < end)
                .order_by(Sample.ts)
                .limit(MAX_HISTORY_ROWS + 1)
            )
        ).all()
        truncated = len(rows) > MAX_HISTORY_ROWS
        raw = [
            HistoryItem(ts=r.ts, value=r.value, status=r.status) for r in rows[:MAX_HISTORY_ROWS]
        ]
        return HistoryOut(point_id=point_id, bucket=None, truncated=truncated, items=raw)

    if (end - start).total_seconds() / seconds > MAX_HISTORY_ROWS:
        raise HTTPException(UNPROCESSABLE, "trop de tranches : augmentez le bucket")
    # `time_bucket` (TimescaleDB) et `date_bin` (PostgreSQL 14+) : mêmes tranches (même origine).
    function = "time_bucket" if getattr(request.app.state, "use_time_bucket", False) else "date_bin"
    aggregated: Any = await session.execute(
        text(
            f"""
            SELECT {function}(make_interval(secs => :secs), ts, TIMESTAMPTZ '2000-01-01') AS bucket,
                   avg(value) AS avg, min(value) AS min, max(value) AS max, count(*) AS count
            FROM sample
            WHERE point_id = :point_id AND ts >= :start AND ts < :end
            GROUP BY 1 ORDER BY 1
            """
        ),
        {"secs": seconds, "point_id": point_id, "start": start, "end": end},
    )
    items = [
        HistoryItem(ts=r.bucket, value=r.avg, min=r.min, max=r.max, count=r.count)
        for r in aggregated
    ]
    return HistoryOut(
        point_id=point_id, bucket=format_bucket(seconds), truncated=False, items=items
    )


_UPDATABLE = ("path", "deadband", "max_interval_s", "poll_interval_s", "write_min", "write_max")
_NOT_NULLABLE = ("deadband", "max_interval_s")


@router.patch("/points/{point_id}", response_model=PointDetail)
async def update_point(
    point_id: uuid.UUID,
    body: PointUpdate,
    request: Request,
    session: SessionDep,
    redis: RedisDep,
    user: Annotated[CurrentUser, Depends(require_role(RoleName.ENGINEER))],
) -> PointDetail:
    """Règle un point : deadband, intervalles, bornes d'écriture, chemin logique, tags."""
    point = await session.get(Point, point_id)
    if point is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "point introuvable")
    requested = body.model_dump(exclude_unset=True)
    for field in _NOT_NULLABLE:
        if field in requested and requested[field] is None:
            raise HTTPException(UNPROCESSABLE, f"{field} ne peut pas être vide")

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for field in _UPDATABLE:
        if field in requested and getattr(point, field) != requested[field]:
            before[field], after[field] = getattr(point, field), requested[field]
            setattr(point, field, requested[field])
    if (
        point.write_min is not None
        and point.write_max is not None
        and point.write_min > point.write_max
    ):
        raise HTTPException(UNPROCESSABLE, "write_min ne peut pas dépasser write_max")

    if body.tags is not None:
        wanted = sorted({tag.strip().lower() for tag in body.tags if tag.strip()})
        current = sorted(
            (await session.scalars(select(PointTag.tag).where(PointTag.point_id == point_id))).all()
        )
        if wanted != current:
            before["tags"], after["tags"] = current, wanted
            await session.execute(delete(PointTag).where(PointTag.point_id == point_id))
            session.add_all(PointTag(point_id=point_id, tag=tag) for tag in wanted)

    if after:
        record_audit(
            session,
            request,
            user_id=user.id,
            action="point.update",
            target=point.path or str(point_id),
            before=before,
            after=after,
        )
        await session.commit()
        if any(field in after for field in ("deadband", "max_interval_s", "poll_interval_s")):
            try:
                await redis.publish(CHANNEL_POINT_CONFIG, json.dumps({"point_id": str(point_id)}))
            except Exception:  # le collecteur relira le réglage à la prochaine découverte
                log.warning("notification du collecteur impossible", exc_info=True)
    detail = PointDetail.model_validate(point)
    await _decorate(session, [detail])
    return detail
