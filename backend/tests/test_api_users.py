"""Utilisateurs, rôles et découverte manuelle (vrai serveur uvicorn)."""

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import AuditLog
from tests.api_harness import PASSWORD, logged_in, make_user, running_api

pytestmark = pytest.mark.integration


@pytest.fixture
async def api(db_engine: AsyncEngine, redis: Any):  # type: ignore[no-untyped-def]
    async with running_api(db_engine, redis) as server:
        yield server


async def audit_actions(sessions: async_sessionmaker[AsyncSession]) -> list[str]:
    async with sessions() as session:
        rows = await session.scalars(select(AuditLog.action).order_by(AuditLog.id))
        return list(rows)


async def test_only_admins_manage_users_and_roles(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "eve", "engineer")
    async with api.client() as anonymous:
        assert (await anonymous.get("/users")).status_code == 401
    async with logged_in(api, "eve") as client:
        for method, path in [("GET", "/users"), ("GET", "/roles"), ("POST", "/users")]:
            response = await client.request(method, path, json={})
            assert response.status_code == 403, path


async def test_roles_are_listed_from_viewer_to_admin(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "root", "admin")
    async with logged_in(api, "root") as client:
        roles = (await client.get("/roles")).json()
    assert [r["name"] for r in roles] == ["viewer", "operator", "engineer", "admin"]
    assert [r["level"] for r in roles] == [1, 2, 3, 4]


async def test_admin_creates_a_user_who_can_log_in(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "root", "admin")
    async with logged_in(api, "root") as admin:
        created = await admin.post(
            "/users",
            json={"login": "nouveau.op", "password": "un-mot-de-passe-long", "role": "operator"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["login"] == "nouveau.op" and body["role"] == "operator" and body["active"]
        assert "password" not in created.text and "hash" not in created.text
        duplicate = await admin.post(
            "/users",
            json={"login": "nouveau.op", "password": "un-mot-de-passe-long", "role": "viewer"},
        )
        assert duplicate.status_code == 409
        listed = {u["login"]: u for u in (await admin.get("/users")).json()}
        assert listed["nouveau.op"]["role"] == "operator" and listed["root"]["role"] == "admin"
    async with api.client() as client:
        login = await client.post(
            "/auth/login", json={"login": "nouveau.op", "password": "un-mot-de-passe-long"}
        )
    assert login.status_code == 200 and login.json()["role"] == "operator"
    actions = await audit_actions(sessions)
    assert "user.create" in actions


@pytest.mark.parametrize(
    "payload",
    [
        {"login": "ok", "password": "court", "role": "viewer"},  # mot de passe trop court
        {"login": "espace interdit", "password": "un-mot-de-passe-long", "role": "viewer"},
        {"login": "ok", "password": "un-mot-de-passe-long", "role": "superuser"},
        {"login": "ok", "password": "un-mot-de-passe-long", "role": "viewer", "active": False},
    ],
)
async def test_invalid_user_payloads_are_rejected(
    api: Any, sessions: async_sessionmaker[AsyncSession], payload: dict[str, Any]
) -> None:
    await make_user(sessions, "root", "admin")
    async with logged_in(api, "root") as admin:
        assert (await admin.post("/users", json=payload)).status_code == 422


async def test_role_change_and_deactivation_apply_at_once(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "root", "admin")
    target = await make_user(sessions, "op", "operator")
    async with logged_in(api, "root") as admin, logged_in(api, "op") as op:
        assert (await op.get("/alarm-rules")).status_code == 403
        promoted = await admin.patch(f"/users/{target}", json={"role": "engineer"})
        assert promoted.status_code == 200 and promoted.json()["role"] == "engineer"
        assert (await op.get("/alarm-rules")).status_code == 200  # droits relus à chaque requête
        await admin.patch(f"/users/{target}", json={"active": False})
        assert (await op.get("/alarm-rules")).status_code == 401
        assert (await admin.patch(f"/users/{target}", json={})).status_code == 422


async def test_password_reset_by_admin_cuts_the_old_session(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "root", "admin")
    target = await make_user(sessions, "op", "operator")
    async with logged_in(api, "root") as admin, logged_in(api, "op") as op:
        assert (await op.get("/auth/me")).status_code == 200
        reset = await admin.patch(f"/users/{target}", json={"password": "nouveau-secret-123"})
        assert reset.status_code == 200
        assert (await op.get("/auth/me")).status_code == 401
    async with api.client() as client:
        old = await client.post("/auth/login", json={"login": "op", "password": PASSWORD})
        new = await client.post(
            "/auth/login", json={"login": "op", "password": "nouveau-secret-123"}
        )
    assert (old.status_code, new.status_code) == (401, 200)
    async with sessions() as session:
        row = await session.scalar(select(AuditLog).where(AuditLog.action == "user.update"))
    assert row is not None and row.after == {
        "role": "operator",
        "active": True,
        "password_changed": True,
    }
    assert "nouveau-secret" not in str(row.after) + str(row.before)


async def test_an_admin_cannot_lock_themselves_out_or_leave_none(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    me = await make_user(sessions, "root", "admin")
    other = await make_user(sessions, "second", "admin")
    async with logged_in(api, "root") as admin:
        assert (await admin.patch(f"/users/{me}", json={"role": "viewer"})).status_code == 409
        assert (await admin.patch(f"/users/{me}", json={"active": False})).status_code == 409
        assert (await admin.delete(f"/users/{me}")).status_code == 409
        # Un autre admin peut être rétrogradé tant qu'il en reste un…
        assert (await admin.patch(f"/users/{other}", json={"role": "engineer"})).status_code == 200
        # …et supprimé s'il n'est plus admin.
        assert (await admin.delete(f"/users/{other}")).status_code == 204
        assert (await admin.delete(f"/users/{other}")).status_code == 404


async def test_the_last_active_admin_is_protected_from_another_admin(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "root", "admin")
    other = await make_user(sessions, "second", "admin")
    async with logged_in(api, "root") as admin:
        await admin.patch(f"/users/{other}", json={"active": False})  # second devient inactif
        # root reste le seul admin actif : rien ne peut l'enlever, et second inactif peut partir.
        assert (await admin.delete(f"/users/{other}")).status_code == 204


async def test_admin_unlocks_a_locked_account(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "root", "admin")
    target = await make_user(sessions, "op", "operator")
    async with api.client() as client:
        for _ in range(5):
            await client.post("/auth/login", json={"login": "op", "password": "faux"})
        assert (
            await client.post("/auth/login", json={"login": "op", "password": PASSWORD})
        ).status_code == 429
    async with logged_in(api, "root") as admin:
        listed = {u["login"]: u for u in (await admin.get("/users")).json()}
        assert listed["op"]["locked"] is True and listed["root"]["locked"] is False
        assert (await admin.post(f"/users/{target}/unlock")).status_code == 200
        assert not {u["login"]: u for u in (await admin.get("/users")).json()}["op"]["locked"]
    async with api.client() as client:
        assert (
            await client.post("/auth/login", json={"login": "op", "password": PASSWORD})
        ).status_code == 200
    assert "user.unlock" in await audit_actions(sessions)


async def test_discovery_run_needs_a_collector_and_an_engineer(
    api: Any, sessions: async_sessionmaker[AsyncSession], redis: Any
) -> None:
    await make_user(sessions, "op", "operator")
    await make_user(sessions, "eng", "engineer")
    async with logged_in(api, "op") as op:
        assert (await op.post("/discovery/run")).status_code == 403
    async with logged_in(api, "eng") as eng:
        # Aucun collecteur à l'écoute : 503, sans faire croire à une relance.
        assert (await eng.post("/discovery/run")).status_code == 503
        pubsub = redis.pubsub()
        await pubsub.subscribe("discovery.run")
        accepted = await eng.post("/discovery/run")
        assert accepted.status_code == 202 and accepted.json() == {"requested": True}
        await pubsub.aclose()
    assert "discovery.run" in await audit_actions(sessions)


async def test_audit_action_filter_matches_a_prefix(
    api: Any, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(sessions, "root", "admin")
    async with logged_in(api, "root") as admin:
        await admin.post(
            "/users", json={"login": "x1", "password": "un-mot-de-passe-long", "role": "viewer"}
        )
        prefix = (await admin.get("/audit", params={"action": "user."})).json()
        exact = (await admin.get("/audit", params={"action": "user.create"})).json()
        other = (await admin.get("/audit", params={"action": "synoptic."})).json()
    assert [row["action"] for row in prefix] == ["user.create"]
    assert [row["action"] for row in exact] == ["user.create"]
    assert other == []
