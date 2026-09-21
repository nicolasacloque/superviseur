"""Génère `docs/API.md` depuis l'OpenAPI : `python -m app.api.apidoc > ../docs/API.md`.

Le rôle minimal de chaque route est lu dans ses dépendances FastAPI (`require_role`,
`current_user`) : la documentation ne peut pas diverger des droits réellement appliqués.
"""

from __future__ import annotations

import sys
from typing import Any

from fastapi.routing import APIRoute
from pydantic import SecretStr

from app.api.deps import current_user
from app.api.health import router as health_router
from app.api.main import API_MODULES, API_PREFIX, create_app
from app.auth.permissions import ROLE_LEVEL
from app.common.config import Settings

PUBLIC = "public"
AUTHENTICATED = "viewer"
METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}


def _walk(dependant: Any) -> list[Any]:
    calls: list[Any] = []
    for sub in dependant.dependencies:
        calls.append(sub.call)
        calls.extend(_walk(sub))
    return calls


def required_role(route: APIRoute, router_dependencies: list[Any] | None = None) -> str:
    """`public`, ou le rôle minimal exigé (`viewer` = tout compte connecté).

    `router_dependencies` : dépendances posées sur tout le routeur (`APIRouter(dependencies=)`).
    """
    calls = [d.dependency for d in [*(router_dependencies or []), *route.dependencies]]
    calls += _walk(route.dependant)
    best = 0
    declared = getattr(route.endpoint, "required_role", None)
    if declared is not None:
        best = ROLE_LEVEL[declared.value]
    for call in calls:
        if call is current_user:
            best = max(best, ROLE_LEVEL[AUTHENTICATED])
        elif getattr(call, "__qualname__", "").startswith("require_role.<locals>"):
            minimum = call.__closure__[0].cell_contents
            best = max(best, ROLE_LEVEL[minimum.value])
    if best == 0:
        return PUBLIC
    return next(name for name, level in ROLE_LEVEL.items() if level == best)


def _type(schema: dict[str, Any], spec: dict[str, Any]) -> str:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "anyOf" in schema:
        return " | ".join(_type(s, spec) for s in schema["anyOf"])
    if schema.get("enum"):
        return " | ".join(f'"{v}"' for v in schema["enum"])
    kind = schema.get("type", "objet")
    if kind == "array":
        return f"liste de {_type(schema.get('items', {}), spec)}"
    return str(kind)


def _fields(name: str, spec: dict[str, Any]) -> list[str]:
    schema = spec.get("components", {}).get("schemas", {}).get(name)
    if not schema or "properties" not in schema:
        return []
    required = set(schema.get("required", []))
    return [
        f"`{field}`{'' if field in required else ' (facultatif)'} : {_type(prop, spec)}"
        for field, prop in schema["properties"].items()
    ]


def _body(operation: dict[str, Any], spec: dict[str, Any]) -> str | None:
    content = operation.get("requestBody", {}).get("content", {}).get("application/json")
    return _type(content["schema"], spec) if content else None


def _response(operation: dict[str, Any], spec: dict[str, Any]) -> str:
    codes = []
    for code, response in operation.get("responses", {}).items():
        if code == "422":
            continue
        content = response.get("content", {}).get("application/json")
        codes.append(f"{code} ({_type(content['schema'], spec)})" if content else code)
    return ", ".join(codes)


def route_roles() -> dict[tuple[str, str], str]:
    """Rôle minimal de chaque route REST, indexé par (méthode, chemin complet)."""
    roles: dict[tuple[str, str], str] = {}
    for module in API_MODULES:
        for route in module.router.routes:
            if isinstance(route, APIRoute):
                for method in route.methods or ():
                    roles[(method, f"{API_PREFIX}{route.path}")] = required_role(
                        route, module.router.dependencies
                    )
    for route in health_router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                roles[(method, f"{API_PREFIX}{route.path}")] = required_role(route)
    return roles


def build_markdown() -> str:
    app = create_app(settings=Settings(_env_file=None, jwt_secret=SecretStr("x" * 32)))
    spec = app.openapi()
    roles = route_roles()

    groups: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            tag = (operation.get("tags") or ["divers"])[0]
            groups.setdefault(tag, []).append((method.upper(), path, operation))

    lines = [
        "# API REST",
        "",
        "Généré depuis l'OpenAPI (`python -m app.api.apidoc`) : ne pas modifier à la main.",
        "Documentation interactive sur `/api/docs`, schéma brut sur `/api/openapi.json`.",
        "",
        "Authentification : cookie `access_token` (JWT, HttpOnly, 15 min) obtenu par",
        "`POST /api/v1/auth/login`, renouvelé par `POST /api/v1/auth/refresh`.",
        "Rôle minimal : `public` (aucun), `viewer` (tout compte",
        "connecté), `operator`, `engineer`, `admin` ; un rôle inclut ceux qui le précèdent.",
        'Réponses d\'erreur : `{"detail": ...}` ; 401 non authentifié, 403 droits insuffisants,',
        "422 requête invalide, 429 compte verrouillé.",
    ]
    for tag in sorted(groups):
        lines += ["", f"## {tag}"]
        for method, path, operation in sorted(
            groups[tag], key=lambda e: (e[1], METHOD_ORDER.get(e[0], 9))
        ):
            role = roles.get((method, path), PUBLIC)
            lines += ["", f"### `{method} {path}`", "", f"Rôle minimal : **{role}**"]
            summary = (operation.get("description") or "").strip().split("\n")[0]
            if summary:
                lines += ["", summary]
            params = operation.get("parameters", [])
            if params:
                lines += ["", "| Paramètre | Où | Type | Requis |", "|---|---|---|---|"]
                for p in params:
                    kind = _type(p.get("schema", {}), spec)
                    required = "oui" if p.get("required") else "non"
                    lines.append(f"| `{p['name']}` | {p['in']} | {kind} | {required} |")
            body = _body(operation, spec)
            if body:
                lines += ["", f"Corps JSON : `{body}`"]
                lines += [f"- {field}" for field in _fields(body, spec)]
            responses = _response(operation, spec)
            if responses:
                lines += ["", f"Réponses : {responses}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.stdout.write(build_markdown())
