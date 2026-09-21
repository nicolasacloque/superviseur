"""Jetons JWT : accès court (15 min) et refresh (7 jours)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt

TokenKind = Literal["access", "refresh"]
ALGORITHM = "HS256"


class TokenError(Exception):
    """Jeton absent, invalide, expiré ou d'un autre type."""


def password_stamp(password_hash: str) -> str:
    """Empreinte du mot de passe courant : changer le mot de passe invalide les jetons déjà émis."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class TokenClaims:
    user_id: uuid.UUID
    kind: TokenKind
    expires_at: datetime
    stamp: str = ""


def create_token(
    secret: str,
    user_id: uuid.UUID,
    kind: TokenKind,
    ttl: timedelta,
    now: datetime | None = None,
    stamp: str = "",
) -> str:
    issued = now or datetime.now(UTC)
    payload = {"sub": str(user_id), "typ": kind, "iat": issued, "exp": issued + ttl, "pwd": stamp}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(secret: str, token: str | None, expected: TokenKind) -> TokenClaims:
    if not token:
        raise TokenError("jeton absent")
    try:
        payload = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub", "typ"]}
        )
        if payload["typ"] != expected:
            raise TokenError("type de jeton inattendu")
        return TokenClaims(
            user_id=uuid.UUID(payload["sub"]),
            kind=expected,
            expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
            stamp=str(payload.get("pwd", "")),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise TokenError(str(exc)) from exc
