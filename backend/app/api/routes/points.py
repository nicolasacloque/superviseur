"""Points : recherche paginée, fiche avec dernière valeur, historique."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import ColumnElement, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep, require_role
from app.api.schemas import HistoryItem, HistoryOut, LatestOut, PointDetail, PointOut, PointPage
from app.common.models import RoleName
from app.db.models import Point, PointLatest, PointTag, Sample

UNPROCESSABLE = 422  # nom du code HTTP variable selon les versions de Starlette

router = APIRouter(tags=["points"], dependencies=[Depends(require_role(RoleName.VIEWER))])

MAX_PAGE = 500
MAX_HISTORY_ROWS = 20_000
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
        raise HTTPException(UNPROCESSABLE, "bucket invalide (exemples : 30s, 5m, 1h, 1d)")
    return seconds


@router.get("/points/{point_id}/history", response_model=HistoryOut)
async def point_history(
    point_id: uuid.UUID,
    session: SessionDep,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    bucket: Annotated[
        str | None, Query(description="ex. 5m, 1h : agrégation moyenne/min/max")
    ] = None,
) -> HistoryOut:
    if await session.get(Point, point_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "point introuvable")
    end = to or datetime.now(UTC)
    start = from_ or end - timedelta(hours=24)
    if start >= end:
        raise HTTPException(UNPROCESSABLE, "`from` doit précéder `to`")

    items: list[HistoryItem]
    if bucket is None:
        rows = (
            await session.scalars(
                select(Sample)
                .where(Sample.point_id == point_id, Sample.ts >= start, Sample.ts < end)
                .order_by(Sample.ts)
                .limit(MAX_HISTORY_ROWS + 1)
            )
        ).all()
        truncated = len(rows) > MAX_HISTORY_ROWS
        items = [
            HistoryItem(ts=r.ts, value=r.value, status=r.status) for r in rows[:MAX_HISTORY_ROWS]
        ]
        return HistoryOut(point_id=point_id, bucket=None, truncated=truncated, items=items)

    seconds = _parse_bucket(bucket)
    if (end - start).total_seconds() / seconds > MAX_HISTORY_ROWS:
        raise HTTPException(UNPROCESSABLE, "trop de tranches : augmentez le bucket")
    aggregated: Any = await session.execute(
        text(
            """
            SELECT date_bin(make_interval(secs => :secs), ts, TIMESTAMPTZ '2000-01-01') AS bucket,
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
    return HistoryOut(point_id=point_id, bucket=bucket, truncated=False, items=items)
