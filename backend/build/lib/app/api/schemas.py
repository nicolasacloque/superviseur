"""Schémas de réponse et de requête de l'API."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.alarms.channels import valid_webhook_url
from app.alarms.rules import THRESHOLD_KINDS
from app.common.models import AlarmKind, Severity


class UserOut(BaseModel):
    id: uuid.UUID
    login: str
    role: str


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class NetworkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    bind_ip: str
    port: int
    bbmd_ip: str | None
    bbmd_ttl: int | None


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    network_id: uuid.UUID
    instance: int
    name: str
    address: str
    online: bool
    last_seen: datetime | None
    vendor: str | None
    model: str | None
    point_count: int = 0


class LatestOut(BaseModel):
    ts: datetime
    value: float | None
    status: str


class PointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    name: str
    description: str | None
    object_type: str
    object_instance: int
    unit: str | None
    writable: bool
    cov_capable: bool
    missing: bool
    path: str | None
    tags: list[str] = Field(default_factory=list)
    latest: LatestOut | None = None


class PointDetail(PointOut):
    state_text: list[str] | None
    write_min: float | None
    write_max: float | None
    deadband: float
    max_interval_s: int
    poll_interval_s: int | None


class PointPage(BaseModel):
    items: list[PointOut]
    total: int
    limit: int
    offset: int


class PointUpdate(BaseModel):
    """Réglage d'un point ; les champs absents restent inchangés, `null` efface (si permis)."""

    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(default=None, max_length=512)
    deadband: float | None = Field(default=None, ge=0)
    max_interval_s: int | None = Field(default=None, ge=1, le=86400)
    poll_interval_s: int | None = Field(default=None, ge=1, le=86400)
    write_min: float | None = None
    write_max: float | None = None
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(
        default=None, max_length=32
    )


class HistoryItem(BaseModel):
    ts: datetime
    value: float | None
    status: str | None = None
    min: float | None = None
    max: float | None = None
    count: int | None = None


class HistoryOut(BaseModel):
    point_id: uuid.UUID
    bucket: str | None
    truncated: bool
    items: list[HistoryItem]


class WriteRequest(BaseModel):
    # `null` relâche la priorité ; un binaire s'écrit 0 ou 1 (true/false acceptés).
    value: float | None
    priority: int = Field(default=8, ge=1, le=16)


class WriteResponse(BaseModel):
    status: Literal["ok"]
    command_id: str


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    user_id: uuid.UUID | None
    action: str
    target: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    ip: str | None


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_CHANNELS = 10


def validate_channel(channel: str) -> str:
    """`email` (destinataires par défaut), `email:adresse` ou `webhook:https://...`."""
    kind, _, target = channel.partition(":")
    if kind == "email" and (target == "" or _EMAIL.match(target)):
        return channel
    if kind == "webhook" and valid_webhook_url(target):
        return channel
    raise ValueError(f"canal invalide : {channel!r} (email, email:adresse ou webhook:url)")


class AlarmRuleIn(BaseModel):
    """Règle d'alarme (section 9.1)."""

    model_config = ConfigDict(extra="forbid")

    point_id: uuid.UUID
    name: str | None = Field(default=None, max_length=255)
    kind: AlarmKind
    # high/low : seuil ; state : valeur attendue (ex. 1) ; stale : durée maximale en secondes.
    threshold: float | None = None
    hysteresis: float = Field(default=0.0, ge=0)
    delay_s: int = Field(default=0, ge=0, le=86400)
    severity: Severity
    notify: list[str] = Field(default_factory=list, max_length=MAX_CHANNELS)
    enabled: bool = True

    @field_validator("notify")
    @classmethod
    def _channels(cls, value: list[str]) -> list[str]:
        return [validate_channel(channel) for channel in value]

    @model_validator(mode="after")
    def _threshold_required(self) -> AlarmRuleIn:
        if self.kind in THRESHOLD_KINDS and self.threshold is None:
            raise ValueError(f"threshold est obligatoire pour une règle {self.kind.value}")
        if self.kind is AlarmKind.STALE and (self.threshold or 0) <= 0:
            raise ValueError("threshold (secondes) doit être positif pour une règle stale")
        return self


class AlarmRuleUpdate(BaseModel):
    """Modification partielle : les champs absents restent inchangés."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    kind: AlarmKind | None = None
    threshold: float | None = None
    hysteresis: float | None = Field(default=None, ge=0)
    delay_s: int | None = Field(default=None, ge=0, le=86400)
    severity: Severity | None = None
    notify: list[str] | None = Field(default=None, max_length=MAX_CHANNELS)
    enabled: bool | None = None


class AlarmRuleOut(BaseModel):
    id: uuid.UUID
    point_id: uuid.UUID
    point_name: str
    path: str | None
    name: str | None
    kind: str
    threshold: float | None
    hysteresis: float
    delay_s: int
    severity: str
    notify: list[str]
    enabled: bool


class AlarmOut(BaseModel):
    id: uuid.UUID
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
    acked_at: datetime | None
    acked_by: str | None
    cleared_at: datetime | None


class AlarmPage(BaseModel):
    items: list[AlarmOut]
    total: int
    limit: int
    offset: int


class AckResponse(BaseModel):
    status: Literal["ok"]
    alarm: AlarmOut
