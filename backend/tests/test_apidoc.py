"""Droits par route : rien n'est public par accident, et docs/API.md est à jour."""

from pathlib import Path

from app.api.apidoc import build_markdown, route_roles

# Seules routes accessibles sans session : la connexion elle-même et l'état des services.
PUBLIC_ROUTES = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/refresh"),  # vérifie son propre cookie de renouvellement
    ("GET", "/api/v1/health"),
}
ADMIN_ONLY_PREFIXES = ("/api/v1/users", "/api/v1/roles", "/api/v1/audit")


def test_every_route_requires_authentication_except_the_allowlist() -> None:
    roles = route_roles()
    assert len(roles) > 30  # garde-fou : l'énumération des routes n'est pas vide
    public = {key for key, role in roles.items() if role == "public"}
    assert public == PUBLIC_ROUTES


def test_user_audit_and_role_routes_are_admin_only() -> None:
    for (method, path), role in route_roles().items():
        if path.startswith(ADMIN_ONLY_PREFIXES):
            assert role == "admin", (method, path)


def test_writes_need_operator_and_configuration_needs_engineer() -> None:
    roles = route_roles()
    assert roles[("POST", "/api/v1/points/{point_id}/write")] == "operator"
    assert roles[("POST", "/api/v1/alarms/{alarm_id}/ack")] == "operator"
    assert roles[("POST", "/api/v1/discovery/run")] == "engineer"
    assert roles[("POST", "/api/v1/alarm-rules")] == "engineer"
    assert roles[("PUT", "/api/v1/synoptics/{synoptic_id}")] == "engineer"
    assert roles[("GET", "/api/v1/synoptics")] == "viewer"


def test_api_documentation_is_up_to_date() -> None:
    path = Path(__file__).resolve().parents[2] / "docs" / "API.md"
    assert path.read_text() == build_markdown(), (
        "docs/API.md périmé : `cd backend && python -m app.api.apidoc > ../docs/API.md`"
    )
