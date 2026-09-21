"""Mots de passe : hachage argon2id."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id par défaut
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def burn_verification(password: str) -> None:
    """Dépense le même temps qu'une vraie vérification (login inconnu : pas d'énumération)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _hasher.hash("mot-de-passe-factice")
    verify_password(_dummy_hash, password)
