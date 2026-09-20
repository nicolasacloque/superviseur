"""Briques d'authentification sans base ni réseau."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from app.api.deps import jwt_secret
from app.auth.passwords import burn_verification, hash_password, verify_password
from app.auth.permissions import has_role
from app.auth.tokens import TokenError, create_token, decode_token
from app.common.config import Settings
from app.common.models import RoleName

SECRET = "s" * 40


def test_passwords_use_argon2id_with_unique_salts() -> None:
    first, second = hash_password("un-mot-de-passe"), hash_password("un-mot-de-passe")
    assert first.startswith("$argon2id$") and first != second
    assert verify_password(first, "un-mot-de-passe")
    assert not verify_password(first, "un-autre")
    assert not verify_password(
        "pas-un-hash", "un-mot-de-passe"
    )  # hash corrompu : refus, pas d'erreur
    burn_verification("peu importe")  # ne lève rien


def test_token_roundtrip_and_kind_are_enforced() -> None:
    user_id = uuid.uuid4()
    access = create_token(SECRET, user_id, "access", timedelta(minutes=15))
    claims = decode_token(SECRET, access, "access")
    assert claims.user_id == user_id and claims.kind == "access"
    assert claims.expires_at > datetime.now(UTC) + timedelta(minutes=14)
    with pytest.raises(TokenError):
        decode_token(SECRET, access, "refresh")  # un jeton d'accès n'ouvre pas le refresh


@pytest.mark.parametrize("token", [None, "", "abc", "a.b.c"])
def test_garbage_tokens_are_rejected(token: str | None) -> None:
    with pytest.raises(TokenError):
        decode_token(SECRET, token, "access")


def test_expired_wrongly_signed_and_unsigned_tokens_are_rejected() -> None:
    user_id = uuid.uuid4()
    expired = create_token(SECRET, user_id, "access", timedelta(seconds=-1))
    other_key = create_token("z" * 40, user_id, "access", timedelta(minutes=5))
    for token in (expired, other_key):
        with pytest.raises(TokenError):
            decode_token(SECRET, token, "access")

    unsigned = jwt.encode(
        {"sub": str(user_id), "typ": "access", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        key=None,
        algorithm="none",
    )
    with pytest.raises(TokenError):
        decode_token(SECRET, unsigned, "access")  # algorithme "none" refusé


def test_role_hierarchy() -> None:
    assert has_role("admin", RoleName.VIEWER) and has_role("admin", RoleName.ADMIN)
    assert has_role("operator", RoleName.VIEWER) and has_role("operator", RoleName.OPERATOR)
    assert not has_role("operator", RoleName.ENGINEER)
    assert not has_role("viewer", RoleName.OPERATOR)
    assert not has_role("inconnu", RoleName.VIEWER)  # un rôle inconnu n'a aucun droit


def test_jwt_secret_must_be_set_and_long_enough() -> None:
    with pytest.raises(RuntimeError, match="pas défini"):
        jwt_secret(Settings(_env_file=None, jwt_secret=None))
    with pytest.raises(RuntimeError, match="32"):
        jwt_secret(Settings(_env_file=None, jwt_secret=SecretStr("trop-court")))
    assert jwt_secret(Settings(_env_file=None, jwt_secret=SecretStr(SECRET))) == SECRET
