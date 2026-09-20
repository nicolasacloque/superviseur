"""Dépendances FastAPI : ressources partagées, utilisateur courant, contrôle des rôles."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import Depends, HTTPException, Request, status
from fastapi.requests import HTTPConnection
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.permissions import has_role
from app.auth.tokens import TokenError, decode_token
from app.common.config import Settings
from app.common.models import RoleName
from app.db.models import Role, User

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"
MIN_JWT_SECRET_LENGTH = 32


def get_settings_dep(conn: HTTPConnection) -> Settings:
    return cast(Settings, conn.app.state.settings)


def get_engine(conn: HTTPConnection) -> AsyncEngine:
    return cast(AsyncEngine, conn.app.state.engine)


def get_redis(conn: HTTPConnection) -> Any:
    # Typé Any : les stubs redis-py rendent `await client.ping()` inutilisable sous mypy strict.
    return conn.app.state.redis


def get_sessions(conn: HTTPConnection) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", conn.app.state.sessions)


async def get_session(
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessions)],
) -> AsyncIterator[AsyncSession]:
    async with sessions() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
RedisDep = Annotated[Any, Depends(get_redis)]


def jwt_secret(settings: Settings) -> str:
    """Secret de signature ; refuse une valeur absente ou trop courte."""
    if settings.jwt_secret is None:
        raise RuntimeError("JWT_SECRET n'est pas défini")
    secret = settings.jwt_secret.get_secret_value()
    if len(secret) < MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(f"JWT_SECRET doit faire au moins {MIN_JWT_SECRET_LENGTH} caractères")
    return secret


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    login: str
    role: str


async def load_user(session: AsyncSession, user_id: uuid.UUID) -> CurrentUser | None:
    """Recharge l'utilisateur en base : un compte désactivé perd l'accès immédiatement."""
    row = (
        await session.execute(
            select(User.id, User.login, Role.name)
            .join(Role, User.role_id == Role.id)
            .where(User.id == user_id, User.active.is_(True))
        )
    ).first()
    return CurrentUser(row[0], row[1], row[2]) if row else None


async def current_user(request: Request, session: SessionDep, settings: SettingsDep) -> CurrentUser:
    try:
        claims = decode_token(jwt_secret(settings), request.cookies.get(ACCESS_COOKIE), "access")
    except TokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentification requise") from None
    user = await load_user(session, claims.user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentification requise")
    return user


UserDep = Annotated[CurrentUser, Depends(current_user)]


def require_role(minimum: RoleName) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    async def dependency(user: UserDep) -> CurrentUser:
        if not has_role(user.role, minimum):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "droits insuffisants")
        return user

    return dependency
