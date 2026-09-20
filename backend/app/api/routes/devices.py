"""Réseaux et équipements."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select

from app.api.deps import SessionDep, require_role
from app.api.schemas import DeviceOut, NetworkOut
from app.common.models import RoleName
from app.db.models import Device, Network, Point

router = APIRouter(tags=["devices"], dependencies=[Depends(require_role(RoleName.VIEWER))])


def _to_out(device: Device, count: int) -> DeviceOut:
    out = DeviceOut.model_validate(device)
    out.point_count = count
    return out


@router.get("/networks", response_model=list[NetworkOut])
async def list_networks(session: SessionDep) -> list[Network]:
    return list((await session.scalars(select(Network).order_by(Network.name))).all())


@router.get("/devices", response_model=list[DeviceOut])
async def list_devices(
    session: SessionDep,
    network: Annotated[uuid.UUID | None, Query(description="filtrer par réseau")] = None,
    online: bool | None = None,
) -> list[DeviceOut]:
    query = select(Device, func.count(Point.id)).outerjoin(
        Point, and_(Point.device_id == Device.id, Point.missing.is_(False))
    )
    if network is not None:
        query = query.where(Device.network_id == network)
    if online is not None:
        query = query.where(Device.online.is_(online))
    rows = (await session.execute(query.group_by(Device.id).order_by(Device.instance))).all()
    return [_to_out(device, count) for device, count in rows]


@router.get("/devices/{device_id}", response_model=DeviceOut)
async def get_device(device_id: uuid.UUID, session: SessionDep) -> DeviceOut:
    query = (
        select(Device, func.count(Point.id))
        .outerjoin(Point, and_(Point.device_id == Device.id, Point.missing.is_(False)))
        .where(Device.id == device_id)
        .group_by(Device.id)
    )
    row = (await session.execute(query)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "équipement introuvable")
    return _to_out(row[0], row[1])
