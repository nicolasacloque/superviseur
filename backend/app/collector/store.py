"""Accès base de données du collecteur (SQLAlchemy asynchrone)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collector.config import NetworkConfig
from app.collector.driver_base import DeviceInfo, ObjectInfo, PointRef
from app.collector.registry import DeviceState, PointState
from app.db.models import (
    AuditLog,
    Device,
    Network,
    Point,
    PointLatest,
    Role,
    Sample,
    User,
)

_POINT_CHUNK = 500
_SAMPLE_CHUNK = 1000


@dataclass(frozen=True)
class WriteTarget:
    """Ce qu'il faut savoir d'un point pour valider et exécuter une écriture."""

    ref: PointRef
    writable: bool
    write_min: float | None
    write_max: float | None
    path: str | None
    last_value: float | None


class Store(Protocol):
    async def ensure_network(self, config: NetworkConfig) -> uuid.UUID: ...

    async def upsert_device(self, network_id: uuid.UUID, info: DeviceInfo) -> uuid.UUID: ...

    async def upsert_points(
        self,
        device_id: uuid.UUID,
        objects: Sequence[ObjectInfo],
        cov_capable: bool,
        path_prefix: str,
    ) -> None: ...

    async def load_devices(
        self, network_id: uuid.UUID, default_poll_interval_s: float
    ) -> list[DeviceState]: ...

    async def set_device_status(
        self, device_id: uuid.UUID, online: bool, last_seen: datetime | None
    ) -> None: ...

    async def write_samples(
        self, samples: Sequence[dict[str, Any]], latest: Sequence[dict[str, Any]]
    ) -> None: ...

    async def get_write_target(self, point_id: uuid.UUID) -> WriteTarget | None: ...

    async def get_user_role(self, user_id: uuid.UUID) -> str | None: ...

    async def add_audit(
        self,
        user_id: uuid.UUID | None,
        action: str,
        target: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None: ...


class DbStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_network(self, config: NetworkConfig) -> uuid.UUID:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(select(Network).where(Network.name == config.name))
            bbmd_ip = config.bbmd.address if config.bbmd.enabled else None
            bbmd_ttl = config.bbmd.ttl if config.bbmd.enabled else None
            if existing is None:
                existing = Network(
                    name=config.name,
                    bind_ip=config.bind_ip,
                    port=config.port,
                    bbmd_ip=bbmd_ip,
                    bbmd_ttl=bbmd_ttl,
                )
                session.add(existing)
                await session.flush()
            else:
                existing.bind_ip = config.bind_ip
                existing.port = config.port
                existing.bbmd_ip = bbmd_ip
                existing.bbmd_ttl = bbmd_ttl
            return existing.id

    async def upsert_device(self, network_id: uuid.UUID, info: DeviceInfo) -> uuid.UUID:
        values = {
            "network_id": network_id,
            "instance": info.instance,
            "name": info.name or f"device-{info.instance}",
            "address": info.address,
            "online": True,
            "last_seen": datetime.now(UTC),
            "vendor": info.vendor,
            "model": info.model,
        }
        insert = pg_insert(Device).values(values)
        stmt = insert.on_conflict_do_update(
            constraint="uq_device_network_id_instance",
            set_={
                key: insert.excluded[key] for key in values if key not in ("network_id", "instance")
            },
        ).returning(Device.id)
        async with self._sessions() as session, session.begin():
            device_id: uuid.UUID = (await session.execute(stmt)).scalar_one()
            return device_id

    async def upsert_points(
        self,
        device_id: uuid.UUID,
        objects: Sequence[ObjectInfo],
        cov_capable: bool,
        path_prefix: str,
    ) -> None:
        rows = [
            {
                "device_id": device_id,
                "object_type": obj.object_type,
                "object_instance": obj.object_instance,
                "name": obj.name,
                "description": obj.description,
                "unit": obj.unit,
                "writable": obj.writable,
                "cov_capable": cov_capable,
                "state_text": obj.state_text,
                "path": f"{path_prefix}/{obj.name}",
                "missing": False,
            }
            for obj in objects
        ]
        async with self._sessions() as session, session.begin():
            for start in range(0, len(rows), _POINT_CHUNK):
                stmt = pg_insert(Point).values(rows[start : start + _POINT_CHUNK])
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_point_device_object",
                    # `path` n'est jamais écrasé : il peut avoir été édité à la main.
                    set_={
                        key: stmt.excluded[key]
                        for key in (
                            "name",
                            "description",
                            "unit",
                            "writable",
                            "cov_capable",
                            "state_text",
                            "missing",
                        )
                    },
                )
                await session.execute(stmt)
            # Un point disparu n'est jamais supprimé, seulement marqué `missing`.
            seen = {(obj.object_type, obj.object_instance) for obj in objects}
            current = await session.execute(
                select(Point.id, Point.object_type, Point.object_instance).where(
                    Point.device_id == device_id
                )
            )
            gone = [pid for pid, otype, inst in current if (otype, inst) not in seen]
            if gone:
                await session.execute(update(Point).where(Point.id.in_(gone)).values(missing=True))

    async def load_devices(
        self, network_id: uuid.UUID, default_poll_interval_s: float
    ) -> list[DeviceState]:
        async with self._sessions() as session:
            devices = (
                await session.scalars(select(Device).where(Device.network_id == network_id))
            ).all()
            points = (
                await session.scalars(
                    select(Point).where(
                        Point.device_id.in_([d.id for d in devices]), Point.missing.is_(False)
                    )
                )
            ).all()
        states = {
            d.id: DeviceState(
                device_id=d.id,
                instance=d.instance,
                address=d.address,
                name=d.name,
                supports_cov=False,
                online=d.online,
            )
            for d in devices
        }
        for p in points:
            device = states[p.device_id]
            device.supports_cov = device.supports_cov or p.cov_capable
            device.points[p.id] = PointState(
                ref=PointRef(
                    p.id, device.instance, device.address, p.object_type, p.object_instance
                ),
                name=p.name,
                path=p.path,
                deadband=p.deadband,
                max_interval_s=p.max_interval_s,
                poll_interval_s=float(p.poll_interval_s or default_poll_interval_s),
                cov_capable=p.cov_capable,
            )
        return sorted(states.values(), key=lambda d: d.instance)

    async def set_device_status(
        self, device_id: uuid.UUID, online: bool, last_seen: datetime | None
    ) -> None:
        values: dict[str, Any] = {"online": online}
        if last_seen is not None:
            values["last_seen"] = last_seen
        async with self._sessions() as session, session.begin():
            await session.execute(update(Device).where(Device.id == device_id).values(**values))

    async def write_samples(
        self, samples: Sequence[dict[str, Any]], latest: Sequence[dict[str, Any]]
    ) -> None:
        async with self._sessions() as session, session.begin():
            for start in range(0, len(samples), _SAMPLE_CHUNK):
                chunk = samples[start : start + _SAMPLE_CHUNK]
                await session.execute(pg_insert(Sample).values(chunk).on_conflict_do_nothing())
            for start in range(0, len(latest), _SAMPLE_CHUNK):
                stmt = pg_insert(PointLatest).values(latest[start : start + _SAMPLE_CHUNK])
                stmt = stmt.on_conflict_do_update(
                    index_elements=["point_id"],
                    set_={
                        "ts": stmt.excluded.ts,
                        "value": stmt.excluded.value,
                        "status": stmt.excluded.status,
                    },
                    where=PointLatest.ts <= stmt.excluded.ts,
                )
                await session.execute(stmt)

    async def get_write_target(self, point_id: uuid.UUID) -> WriteTarget | None:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(Point, Device)
                    .join(Device, Point.device_id == Device.id)
                    .where(Point.id == point_id)
                )
            ).first()
            if row is None:
                return None
            point, device = row
            last = await session.scalar(
                select(PointLatest.value).where(PointLatest.point_id == point_id)
            )
        ref = PointRef(
            point.id, device.instance, device.address, point.object_type, point.object_instance
        )
        return WriteTarget(ref, point.writable, point.write_min, point.write_max, point.path, last)

    async def get_user_role(self, user_id: uuid.UUID) -> str | None:
        async with self._sessions() as session:
            role: str | None = await session.scalar(
                select(Role.name)
                .join(User, User.role_id == Role.id)
                .where(User.id == user_id, User.active.is_(True))
            )
            return role

    async def add_audit(
        self,
        user_id: uuid.UUID | None,
        action: str,
        target: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                AuditLog(user_id=user_id, action=action, target=target, before=before, after=after)
            )
