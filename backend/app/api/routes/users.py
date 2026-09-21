"""Utilisateurs et rôles (administrateur) : création, changement de rôle, désactivation."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import record_audit
from app.api.deps import CurrentUser, LoginGuardDep, SessionDep, require_role
from app.api.schemas import RoleOut, UserAdminOut, UserCreate, UserUpdate
from app.auth.passwords import hash_password
from app.auth.permissions import ROLE_LEVEL
from app.common.models import RoleName
from app.db.models import Role, User

UNPROCESSABLE = 422  # nom du code HTTP variable selon les versions de Starlette

router = APIRouter(tags=["users"])
Admin = Annotated[CurrentUser, Depends(require_role(RoleName.ADMIN))]

ROLE_DESCRIPTIONS = {
    "viewer": "lecture des points, synoptiques et alarmes",
    "operator": "viewer + écriture de consignes et acquittement des alarmes",
    "engineer": "operator + édition des synoptiques, règles d'alarme et découverte",
    "admin": "tout, dont les utilisateurs, le journal d'audit et la configuration",
}


async def _role_ids(session: AsyncSession) -> dict[str, int]:
    return {name: rid for rid, name in (await session.execute(select(Role.id, Role.name))).all()}


async def _get(session: AsyncSession, user_id: uuid.UUID) -> tuple[User, str]:
    row = (
        await session.execute(
            select(User, Role.name).join(Role, User.role_id == Role.id).where(User.id == user_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "utilisateur introuvable")
    return row[0], row[1]


async def _other_active_admins(session: AsyncSession, user_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count(User.id))
        .join(Role, User.role_id == Role.id)
        .where(Role.name == RoleName.ADMIN.value, User.active.is_(True), User.id != user_id)
    )
    return int(count or 0)


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(_admin: Admin) -> list[RoleOut]:
    return [
        RoleOut(name=name, level=level, description=ROLE_DESCRIPTIONS[name])
        for name, level in sorted(ROLE_LEVEL.items(), key=lambda item: item[1])
    ]


@router.get("/users", response_model=list[UserAdminOut])
async def list_users(
    session: SessionDep, guard: LoginGuardDep, _admin: Admin
) -> list[UserAdminOut]:
    rows = (
        await session.execute(
            select(User, Role.name).join(Role, User.role_id == Role.id).order_by(User.login)
        )
    ).all()
    return [
        UserAdminOut(
            id=user.id,
            login=user.login,
            role=role,
            active=user.active,
            locked=await guard.locked_for(user.login) > 0,
        )
        for user, role in rows
    ]


@router.post("/users", response_model=UserAdminOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate, request: Request, session: SessionDep, admin: Admin
) -> UserAdminOut:
    role_id = (await _role_ids(session)).get(body.role)
    if role_id is None:
        raise HTTPException(UNPROCESSABLE, "rôle inconnu")
    user = User(
        login=body.login, password_hash=hash_password(body.password), role_id=role_id, active=True
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "ce login existe déjà") from None
    record_audit(
        session,
        request,
        user_id=admin.id,
        action="user.create",
        target=body.login,
        after={"role": body.role},
    )
    await session.commit()
    return UserAdminOut(id=user.id, login=user.login, role=body.role, active=True)


@router.patch("/users/{user_id}", response_model=UserAdminOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    request: Request,
    session: SessionDep,
    guard: LoginGuardDep,
    admin: Admin,
) -> UserAdminOut:
    user, role = await _get(session, user_id)
    new_role = body.role if body.role is not None else role
    new_active = body.active if body.active is not None else user.active
    losing_admin = (
        role == RoleName.ADMIN.value
        and user.active
        and (new_role != RoleName.ADMIN.value or not new_active)
    )
    if losing_admin:
        # Un administrateur ne se retire pas ses propres droits, et il en reste toujours un.
        if user.id == admin.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "vous ne pouvez pas retirer vos propres droits d'administrateur",
            )
        if await _other_active_admins(session, user.id) == 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "il doit rester un administrateur actif")
    if user.id == admin.id and not new_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "vous ne pouvez pas désactiver votre compte")
    before = {"role": role, "active": user.active}
    if body.role is not None:
        role_id = (await _role_ids(session)).get(body.role)
        if role_id is None:
            raise HTTPException(UNPROCESSABLE, "rôle inconnu")
        user.role_id = role_id
    if body.active is not None:
        user.active = body.active
    after: dict[str, object] = {"role": new_role, "active": new_active}
    if body.password is not None:
        user.password_hash = hash_password(body.password)  # invalide aussi ses sessions
        after["password_changed"] = True
    record_audit(
        session,
        request,
        user_id=admin.id,
        action="user.update",
        target=user.login,
        before=before,
        after=after,
    )
    await session.commit()
    return UserAdminOut(
        id=user.id,
        login=user.login,
        role=new_role,
        active=new_active,
        locked=await guard.locked_for(user.login) > 0,
    )


@router.post("/users/{user_id}/unlock", response_model=UserAdminOut)
async def unlock_user(
    user_id: uuid.UUID, request: Request, session: SessionDep, guard: LoginGuardDep, admin: Admin
) -> UserAdminOut:
    user, role = await _get(session, user_id)
    was_locked = await guard.unlock(user.login)
    record_audit(
        session,
        request,
        user_id=admin.id,
        action="user.unlock",
        target=user.login,
        after={"was_locked": was_locked},
    )
    await session.commit()
    return UserAdminOut(id=user.id, login=user.login, role=role, active=user.active)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID, request: Request, session: SessionDep, admin: Admin
) -> None:
    user, role = await _get(session, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "vous ne pouvez pas supprimer votre compte")
    if (
        role == RoleName.ADMIN.value
        and user.active
        and await _other_active_admins(session, user.id) == 0
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "il doit rester un administrateur actif")
    record_audit(
        session,
        request,
        user_id=admin.id,
        action="user.delete",
        target=user.login,
        before={"role": role, "active": user.active},
    )
    await session.delete(user)
    await session.commit()
