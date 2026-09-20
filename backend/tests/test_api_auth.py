"""Authentification, rôles et journal d'audit de l'API (vrai serveur uvicorn)."""

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.cli import create_user
from app.auth.tokens import create_token
from app.common.config import get_settings
from app.db.models import AuditLog, User
from tests.api_harness import (
    JWT_SECRET,
    PASSWORD,
    ensure_roles,
    logged_in,
    make_settings,
    make_user,
    running_api,
    seed_points,
)
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(db_engine: AsyncEngine, redis: Any):  # type: ignore[no-untyped-def]
    async with running_api(db_engine, redis) as server:
        yield server


async def audit_rows(sessions: async_sessionmaker[AsyncSession], action: str) -> list[AuditLog]:
    async with sessions() as session:
        return list(await session.scalars(select(AuditLog).where(AuditLog.action == action)))


async def test_login_sets_httponly_samesite_cookies_and_returns_the_user(
    db_engine: AsyncEngine, redis: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "alice", "operator")
    # Configuration de production : cookies Secure.
    secure = make_settings(cookie_secure=True)
    async with running_api(db_engine, redis, secure) as api, api.client() as client:
        response = await client.post("/auth/login", json={"login": "alice", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["login"] == "alice" and response.json()["role"] == "operator"
    cookies = {c.split("=")[0]: c for c in response.headers.get_list("set-cookie")}
    assert set(cookies) == {"access_token", "refresh_token"}
    for header in cookies.values():
        lowered = header.lower()
        assert "httponly" in lowered and "secure" in lowered and "samesite=strict" in lowered
    assert "max-age=900" in cookies["access_token"].lower()  # 15 minutes
    assert "max-age=604800" in cookies["refresh_token"].lower()  # 7 jours
    assert "path=/api/v1/auth" in cookies["refresh_token"].lower()  # refresh isolé


async def test_wrong_credentials_are_refused_and_audited(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "alice", "viewer")
    async with api.client() as client:
        wrong = await client.post("/auth/login", json={"login": "alice", "password": "faux"})
        unknown = await client.post("/auth/login", json={"login": "personne", "password": "faux"})
        # Même réponse : on ne révèle pas si le compte existe.
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
        assert "set-cookie" not in wrong.headers
        ok = await client.post("/auth/login", json={"login": "alice", "password": PASSWORD})
        assert ok.status_code == 200
    results = [
        (r.target, (r.after or {})["result"]) for r in await audit_rows(sessions, "auth.login")
    ]
    assert sorted(results) == [("alice", "ok"), ("alice", "échec"), ("personne", "échec")]


async def test_protected_routes_require_authentication(api: Any) -> None:
    async with api.client() as client:
        for path in ("/points", "/devices", "/networks", "/auth/me", "/audit"):
            assert (await client.get(path)).status_code == 401, path
        assert (
            await client.post(
                "/points/00000000-0000-0000-0000-000000000000/write", json={"value": 1}
            )
        ).status_code == 401
        assert (await client.get("/health")).status_code == 200  # publique


async def test_me_logout_and_refresh(api: Any, sessions: async_sessionmaker[AsyncSession]) -> None:
    await make_user(sessions, "bob", "engineer")
    async with logged_in(api, "bob") as client:
        me = await client.get("/auth/me")
        assert me.json()["login"] == "bob"

        assert (await client.post("/auth/refresh")).status_code == 200  # jeton renouvelé
        assert (await client.get("/auth/me")).status_code == 200

        assert (await client.post("/auth/logout")).status_code == 204
        assert (await client.get("/auth/me")).status_code == 401
        assert (await client.post("/auth/refresh")).status_code == 401


async def test_invalid_expired_and_wrong_type_tokens_are_refused(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await make_user(sessions, "carol", "admin")
    expired = create_token(JWT_SECRET, user_id, "access", timedelta(seconds=-5))
    forged = create_token("un-autre-secret-" + "y" * 32, user_id, "access", timedelta(minutes=5))
    refresh_as_access = create_token(JWT_SECRET, user_id, "refresh", timedelta(minutes=5))
    valid = create_token(JWT_SECRET, user_id, "access", timedelta(minutes=5))
    async with api.client() as client:
        for token, expected in [
            (expired, 401),
            (forged, 401),
            (refresh_as_access, 401),
            ("n'importe quoi", 401),
            (valid, 200),
        ]:
            client.cookies.clear()
            response = await client.get("/auth/me", headers={"Cookie": f"access_token={token}"})
            assert response.status_code == expected, token[:20]


async def test_disabled_account_loses_access_immediately(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    user_id = await make_user(sessions, "dave", "operator")
    async with logged_in(api, "dave") as client:
        assert (await client.get("/points")).status_code == 200
        async with sessions() as session, session.begin():
            await session.execute(update(User).where(User.id == user_id).values(active=False))
        assert (
            await client.get("/points")
        ).status_code == 401  # jeton encore valide, compte inactif
    async with api.client() as fresh:
        response = await fresh.post("/auth/login", json={"login": "dave", "password": PASSWORD})
        assert response.status_code == 401


async def test_audit_is_reserved_to_administrators(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    for name, role in [("v", "viewer"), ("o", "operator"), ("e", "engineer"), ("a", "admin")]:
        await make_user(sessions, name, role)
    expected = {"v": 403, "o": 403, "e": 403, "a": 200}
    for name, status_code in expected.items():
        async with logged_in(api, name) as client:
            assert (await client.get("/audit")).status_code == status_code, name
    async with logged_in(api, "a") as admin:
        rows = (await admin.get("/audit", params={"action": "auth.login"})).json()
        assert len(rows) >= 5 and all(r["action"] == "auth.login" for r in rows)


async def test_viewer_can_read_but_never_write(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seed_points(sessions)
    await make_user(sessions, "vera", "viewer")
    async with logged_in(api, "vera") as client:
        assert (await client.get("/points")).status_code == 200
        response = await client.post(
            f"/points/{ids['Consigne']}/write", json={"value": 21, "priority": 8}
        )
        assert response.status_code == 403
    (row,) = [r for r in await audit_rows(sessions, "point.write")]
    assert row.after is not None and row.after["result"].startswith("refusé")
    assert row.user_id is not None


def test_json_body_is_validated() -> None:
    from app.api.schemas import WriteRequest

    with pytest.raises(ValueError):
        WriteRequest(value=1.0, priority=0)
    with pytest.raises(ValueError):
        WriteRequest(value=1.0, priority=17)
    assert WriteRequest(value=None).priority == 8  # défaut opérateur manuel


async def test_cli_creates_users_who_can_log_in(
    api: Any, sessions: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with sessions() as session, session.begin():
        await ensure_roles(session)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL or "")
    get_settings.cache_clear()
    try:
        assert await create_user("nouveau", "operator", "un-mot-de-passe-long") == "créé"
        with pytest.raises(SystemExit):
            await create_user("nouveau", "operator", "un-mot-de-passe-long")  # déjà présent
        outcome = await create_user("nouveau", "engineer", "un-autre-mot-de-passe", update=True)
        assert outcome == "mis à jour"
        with pytest.raises(SystemExit):
            await create_user("x", "role-inexistant", "un-mot-de-passe-long")
    finally:
        get_settings.cache_clear()

    async with api.client() as client:
        old = await client.post(
            "/auth/login", json={"login": "nouveau", "password": "un-mot-de-passe-long"}
        )
        new = await client.post(
            "/auth/login", json={"login": "nouveau", "password": "un-autre-mot-de-passe"}
        )
    assert old.status_code == 401
    assert new.status_code == 200 and new.json()["role"] == "engineer"
