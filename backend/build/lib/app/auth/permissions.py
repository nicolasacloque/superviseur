"""Hiérarchie des rôles (section 11) : viewer < operator < engineer < admin."""

from __future__ import annotations

from app.common.models import RoleName

ROLE_LEVEL = {
    RoleName.VIEWER.value: 1,
    RoleName.OPERATOR.value: 2,
    RoleName.ENGINEER.value: 3,
    RoleName.ADMIN.value: 4,
}


def has_role(role: str, minimum: RoleName) -> bool:
    """Vrai si `role` est au moins `minimum` ; un rôle inconnu n'a aucun droit."""
    return ROLE_LEVEL.get(role, 0) >= ROLE_LEVEL[minimum.value]
