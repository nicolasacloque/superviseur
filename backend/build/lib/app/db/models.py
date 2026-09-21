"""Modèles SQLAlchemy (section 5 du cahier des charges).

La table `sample` est déclarée ici comme table ordinaire : sa conversion en hypertable
TimescaleDB (chunks, compression, rétention) est faite par la migration 0002.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Noms de contraintes déterministes : les migrations et les modèles restent comparables.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Any:
    return mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)


class User(Base):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = _uuid_pk()
    login: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int] = mapped_column(ForeignKey("role.id"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Network(Base):
    __tablename__ = "network"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128))
    bind_ip: Mapped[str] = mapped_column(String(64))
    port: Mapped[int] = mapped_column(Integer, server_default=text("47808"))
    bbmd_ip: Mapped[str | None] = mapped_column(String(64))
    bbmd_ttl: Mapped[int | None] = mapped_column(Integer)


class Device(Base):
    __tablename__ = "device"
    __table_args__ = (
        UniqueConstraint("network_id", "instance", name="uq_device_network_id_instance"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    network_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("network.id", ondelete="CASCADE"))
    instance: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(128))
    online: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vendor: Mapped[str | None] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))


class Point(Base):
    __tablename__ = "point"
    __table_args__ = (
        UniqueConstraint(
            "device_id", "object_type", "object_instance", name="uq_point_device_object"
        ),
        Index("ix_point_path", "path"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"))
    object_type: Mapped[str] = mapped_column(String(32))
    object_instance: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(32))
    writable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    cov_capable: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    poll_interval_s: Mapped[int | None] = mapped_column(Integer)
    # Chemin logique : Site/Batiment/Etage/Equipement/Point
    path: Mapped[str | None] = mapped_column(String(512))
    # Ajouts déduits du CDC : point disparu (6.3), règles de stockage (5.2),
    # bornes d'écriture (11), libellés multi-états (6.3).
    missing: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    deadband: Mapped[float] = mapped_column(Double, server_default=text("0"))
    max_interval_s: Mapped[int] = mapped_column(Integer, server_default=text("900"))
    write_min: Mapped[float | None] = mapped_column(Double)
    write_max: Mapped[float | None] = mapped_column(Double)
    state_text: Mapped[list[str] | None] = mapped_column(JSONB)


class PointTag(Base):
    __tablename__ = "point_tag"

    point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("point.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(64), primary_key=True)


class Sample(Base):
    __tablename__ = "sample"

    point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("point.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    value: Mapped[float | None] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'ok'"))


class PointLatest(Base):
    __tablename__ = "point_latest"

    point_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("point.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float | None] = mapped_column(Double)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'ok'"))


class AlarmRule(Base):
    __tablename__ = "alarm_rule"

    id: Mapped[uuid.UUID] = _uuid_pk()
    point_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("point.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[float | None] = mapped_column(Double)
    hysteresis: Mapped[float] = mapped_column(Double, server_default=text("0"))
    delay_s: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    severity: Mapped[str] = mapped_column(String(16))
    notify: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class AlarmEvent(Base):
    """Une occurrence d'alarme, mise à jour à chaque transition (section 9.2)."""

    __tablename__ = "alarm_event"
    __table_args__ = (
        # Une seule alarme ouverte par règle : garde-fou contre deux moteurs ou une relance.
        Index(
            "uq_alarm_event_open_rule",
            "rule_id",
            unique=True,
            postgresql_where=text("state <> 'normal'"),
        ),
        Index("ix_alarm_event_state_raised", "state", "raised_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alarm_rule.id", ondelete="CASCADE"))
    raised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acked_by: Mapped[str | None] = mapped_column(String(64))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(32))
    # Valeur du point au déclenchement (contexte des notifications).
    raised_value: Mapped[float | None] = mapped_column(Double)


class Synoptic(Base):
    __tablename__ = "synoptic"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255))
    slug: Mapped[str] = mapped_column(String(128), unique=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))


class SynopticVersion(Base):
    __tablename__ = "synoptic_version"
    __table_args__ = (
        UniqueConstraint("synoptic_id", "version", name="uq_synoptic_version_synoptic_id_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    synoptic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("synoptic.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    # Colonne SQL `json` ; l'attribut s'appelle `content` pour ne pas masquer le module json.
    content: Mapped[dict[str, Any]] = mapped_column("json", JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(255))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(45))
