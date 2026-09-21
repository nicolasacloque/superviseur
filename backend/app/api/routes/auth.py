"""Connexion, déconnexion, renouvellement du jeton."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.audit import record_audit
from app.api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    LoginGuardDep,
    SessionDep,
    SettingsDep,
    UserDep,
    jwt_secret,
    load_user,
)
from app.api.schemas import LoginRequest, UserOut
from app.auth.passwords import burn_verification, verify_password
from app.auth.tokens import TokenError, create_token, decode_token, password_stamp
from app.common.config import Settings
from app.db.models import Role, User

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = "identifiants invalides"
LOCKED = "compte temporairement verrouillé : trop de tentatives, réessayez plus tard"


def _set_cookies(response: Response, settings: Settings, user_id: uuid.UUID, stamp: str) -> None:
    secret = jwt_secret(settings)
    access_ttl = timedelta(minutes=settings.access_token_minutes)
    refresh_ttl = timedelta(days=settings.refresh_token_days)
    flags: dict[str, Any] = {
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": "strict",
    }
    response.set_cookie(
        ACCESS_COOKIE,
        create_token(secret, user_id, "access", access_ttl, stamp=stamp),
        max_age=int(access_ttl.total_seconds()),
        path="/api",
        **flags,
    )
    # Le refresh n'est envoyé qu'aux routes d'authentification.
    response.set_cookie(
        REFRESH_COOKIE,
        create_token(secret, user_id, "refresh", refresh_ttl, stamp=stamp),
        max_age=int(refresh_ttl.total_seconds()),
        path="/api/v1/auth",
        **flags,
    )


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    guard: LoginGuardDep,
) -> UserOut:
    locked_for = await guard.locked_for(body.login)
    if locked_for > 0:
        # Même durée de traitement qu'une vraie tentative, et le bon mot de passe n'y change rien.
        burn_verification(body.password)
        record_audit(
            session,
            request,
            user_id=None,
            action="auth.login",
            target=body.login,
            after={"result": "verrouillé"},
        )
        await session.commit()
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            LOCKED,
            headers={"Retry-After": str(locked_for)},
        )
    row = (
        await session.execute(
            select(User, Role.name)
            .join(Role, User.role_id == Role.id)
            .where(User.login == body.login)
        )
    ).first()
    user, role = (row[0], row[1]) if row else (None, "")
    valid = False
    if user is not None and user.active:
        valid = verify_password(user.password_hash, body.password)
    else:
        burn_verification(body.password)  # même durée que pour un vrai compte
    record_audit(
        session,
        request,
        user_id=user.id if user else None,
        action="auth.login",
        target=body.login,
        after={"result": "ok" if valid else "échec"},
    )
    if not valid or user is None:
        if await guard.record_failure(body.login) > 0:
            record_audit(
                session,
                request,
                user_id=user.id if user else None,
                action="auth.lockout",
                target=body.login,
                after={"failures": settings.login_max_failures, "lock_s": settings.login_lock_s},
            )
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS)
    await session.commit()
    await guard.record_success(body.login)
    _set_cookies(response, settings, user.id, password_stamp(user.password_hash))
    return UserOut(id=user.id, login=user.login, role=role)


@router.post("/refresh", response_model=UserOut)
async def refresh(
    request: Request, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    try:
        claims = decode_token(jwt_secret(settings), request.cookies.get(REFRESH_COOKIE), "refresh")
    except TokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expirée") from None
    user = await load_user(session, claims.user_id, claims.stamp)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expirée")
    _set_cookies(response, settings, user.id, user.stamp)
    return UserOut(id=user.id, login=user.login, role=user.role)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, session: SessionDep) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/api")
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


@router.get("/me", response_model=UserOut)
async def me(user: UserDep) -> UserOut:
    return UserOut(id=user.id, login=user.login, role=user.role)
