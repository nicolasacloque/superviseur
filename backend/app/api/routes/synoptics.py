"""Synoptiques : document JSON versionné, création, sauvegarde, restauration."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import record_audit
from app.api.deps import CurrentUser, SessionDep, require_role
from app.api.synoptic_doc import (
    MAX_DOC_BYTES,
    SynopticDoc,
    is_valid_slug,
    slugify,
    validate_document,
)
from app.common.models import RoleName
from app.db.models import Synoptic, SynopticVersion, User

UNPROCESSABLE = 422  # nom du code HTTP variable selon les versions de Starlette

router = APIRouter(tags=["synoptics"])
Viewer = Depends(require_role(RoleName.VIEWER))
Engineer = Annotated[CurrentUser, Depends(require_role(RoleName.ENGINEER))]


class SynopticSummary(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    version: int
    updated_at: datetime


class SynopticOut(SynopticSummary):
    doc: dict[str, Any]


class SynopticCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str | None = Field(default=None, max_length=64)
    doc: dict[str, Any]


class SynopticSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc: dict[str, Any]
    # Version sur laquelle l'éditeur a travaillé : refus (409) si quelqu'un a enregistré depuis.
    base_version: int | None = Field(default=None, ge=1)


class VersionOut(BaseModel):
    version: int
    created_at: datetime
    created_by: str | None


def _validated(raw: dict[str, Any]) -> SynopticDoc:
    if len(json.dumps(raw, separators=(",", ":"))) > MAX_DOC_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "synoptique trop volumineux (6 Mo)")
    try:
        return validate_document(raw)
    except ValidationError as exc:
        raise HTTPException(
            UNPROCESSABLE, exc.errors(include_url=False, include_context=False, include_input=False)
        ) from exc


async def _latest(session: AsyncSession, synoptic_id: uuid.UUID) -> SynopticVersion | None:
    latest: SynopticVersion | None = await session.scalar(
        select(SynopticVersion)
        .where(SynopticVersion.synoptic_id == synoptic_id)
        .order_by(SynopticVersion.version.desc())
        .limit(1)
    )
    return latest


async def _by_slug(session: AsyncSession, slug: str) -> Synoptic:
    synoptic = await session.scalar(select(Synoptic).where(Synoptic.slug == slug))
    if synoptic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "synoptique introuvable")
    return synoptic


async def _by_id(session: AsyncSession, synoptic_id: uuid.UUID) -> Synoptic:
    synoptic = await session.get(Synoptic, synoptic_id)
    if synoptic is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "synoptique introuvable")
    return synoptic


def _out(synoptic: Synoptic, version: SynopticVersion) -> SynopticOut:
    return SynopticOut(
        id=synoptic.id,
        name=synoptic.name,
        slug=synoptic.slug,
        version=version.version,
        updated_at=version.created_at,
        doc=version.content,
    )


async def _add_version(
    session: AsyncSession, synoptic: Synoptic, doc: dict[str, Any], user_id: uuid.UUID
) -> SynopticVersion:
    current = await session.scalar(
        select(func.max(SynopticVersion.version)).where(SynopticVersion.synoptic_id == synoptic.id)
    )
    version = SynopticVersion(
        synoptic_id=synoptic.id, version=(current or 0) + 1, content=doc, created_by=user_id
    )
    session.add(version)
    await session.flush()
    await session.refresh(version)  # created_at posé par la base
    return version


@router.get("/synoptics", response_model=list[SynopticSummary], dependencies=[Viewer])
async def list_synoptics(session: SessionDep) -> list[SynopticSummary]:
    latest = (
        select(
            SynopticVersion.synoptic_id,
            func.max(SynopticVersion.version).label("version"),
        )
        .group_by(SynopticVersion.synoptic_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Synoptic, SynopticVersion)
            .join(latest, latest.c.synoptic_id == Synoptic.id)
            .join(
                SynopticVersion,
                (SynopticVersion.synoptic_id == Synoptic.id)
                & (SynopticVersion.version == latest.c.version),
            )
            .order_by(Synoptic.name)
        )
    ).all()
    return [
        SynopticSummary(
            id=s.id, name=s.name, slug=s.slug, version=v.version, updated_at=v.created_at
        )
        for s, v in rows
    ]


@router.get("/synoptics/{slug}", response_model=SynopticOut, dependencies=[Viewer])
async def get_synoptic(slug: str, session: SessionDep) -> SynopticOut:
    synoptic = await _by_slug(session, slug)
    version = await _latest(session, synoptic.id)
    if version is None:  # ne devrait pas arriver : la création écrit toujours la version 1
        raise HTTPException(status.HTTP_404_NOT_FOUND, "synoptique sans version")
    return _out(synoptic, version)


@router.post("/synoptics", response_model=SynopticOut, status_code=status.HTTP_201_CREATED)
async def create_synoptic(
    body: SynopticCreate, request: Request, session: SessionDep, user: Engineer
) -> SynopticOut:
    doc = _validated(body.doc)
    slug = body.slug or slugify(doc.name)
    if not is_valid_slug(slug):
        raise HTTPException(UNPROCESSABLE, "slug : minuscules, chiffres et tirets")
    if body.slug is None:  # slug dérivé du nom : on l'adapte s'il est déjà pris
        base, suffix = slug, 1
        while await session.scalar(select(Synoptic.id).where(Synoptic.slug == slug)):
            suffix += 1
            slug = f"{base[:60]}-{suffix}"
    synoptic = Synoptic(name=doc.name, slug=slug, owner_id=user.id)
    session.add(synoptic)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, f"le slug « {slug} » existe déjà") from None
    version = await _add_version(session, synoptic, doc.to_json(), user.id)
    record_audit(
        session,
        request,
        user_id=user.id,
        action="synoptic.create",
        target=slug,
        after={"version": version.version, "widgets": len(doc.widgets)},
    )
    await session.commit()
    return _out(synoptic, version)


@router.put("/synoptics/{synoptic_id}", response_model=SynopticOut)
async def save_synoptic(
    synoptic_id: uuid.UUID,
    body: SynopticSave,
    request: Request,
    session: SessionDep,
    user: Engineer,
) -> SynopticOut:
    """Enregistrer = créer une nouvelle version ; les précédentes restent restaurables."""
    doc = _validated(body.doc)
    synoptic = await _by_id(session, synoptic_id)
    latest = await _latest(session, synoptic.id)
    if body.base_version is not None and latest and latest.version != body.base_version:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"enregistré entre-temps (version {latest.version}) : rechargez avant d'enregistrer",
        )
    synoptic.name = doc.name
    try:
        version = await _add_version(session, synoptic, doc.to_json(), user.id)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "enregistrement simultané : réessayez"
        ) from None
    record_audit(
        session,
        request,
        user_id=user.id,
        action="synoptic.save",
        target=synoptic.slug,
        before={"version": latest.version if latest else None},
        after={"version": version.version, "widgets": len(doc.widgets)},
    )
    await session.commit()
    return _out(synoptic, version)


@router.delete("/synoptics/{synoptic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_synoptic(
    synoptic_id: uuid.UUID, request: Request, session: SessionDep, user: Engineer
) -> Response:
    synoptic = await _by_id(session, synoptic_id)
    latest = await _latest(session, synoptic.id)
    record_audit(
        session,
        request,
        user_id=user.id,
        action="synoptic.delete",
        target=synoptic.slug,
        before={"version": latest.version if latest else None},
    )
    await session.execute(delete(SynopticVersion).where(SynopticVersion.synoptic_id == synoptic.id))
    await session.delete(synoptic)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/synoptics/{slug}/versions", response_model=list[VersionOut])
async def list_versions(slug: str, session: SessionDep, _user: Engineer) -> list[VersionOut]:
    synoptic = await _by_slug(session, slug)
    rows = (
        await session.execute(
            select(SynopticVersion, User.login)
            .outerjoin(User, SynopticVersion.created_by == User.id)
            .where(SynopticVersion.synoptic_id == synoptic.id)
            .order_by(SynopticVersion.version.desc())
        )
    ).all()
    return [
        VersionOut(version=v.version, created_at=v.created_at, created_by=login)
        for v, login in rows
    ]


@router.get("/synoptics/{slug}/versions/{version}", response_model=SynopticOut)
async def get_version(slug: str, version: int, session: SessionDep, _user: Engineer) -> SynopticOut:
    synoptic = await _by_slug(session, slug)
    found = await session.scalar(
        select(SynopticVersion).where(
            SynopticVersion.synoptic_id == synoptic.id, SynopticVersion.version == version
        )
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version introuvable")
    return _out(synoptic, found)


@router.post("/synoptics/{synoptic_id}/restore/{version}", response_model=SynopticOut)
async def restore_version(
    synoptic_id: uuid.UUID, version: int, request: Request, session: SessionDep, user: Engineer
) -> SynopticOut:
    """Restaurer recopie une ancienne version dans une nouvelle : l'historique reste intact."""
    synoptic = await _by_id(session, synoptic_id)
    old = await session.scalar(
        select(SynopticVersion).where(
            SynopticVersion.synoptic_id == synoptic_id, SynopticVersion.version == version
        )
    )
    if old is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version introuvable")
    doc = _validated(old.content)
    synoptic.name = doc.name
    created = await _add_version(session, synoptic, doc.to_json(), user.id)
    record_audit(
        session,
        request,
        user_id=user.id,
        action="synoptic.restore",
        target=synoptic.slug,
        before={"restored_version": version},
        after={"version": created.version},
    )
    await session.commit()
    return _out(synoptic, created)
