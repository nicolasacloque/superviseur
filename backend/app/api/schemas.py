"""Schémas de réponse et de requête de l'API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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
