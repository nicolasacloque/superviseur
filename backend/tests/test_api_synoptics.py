"""API des synoptiques : versions, restauration, droits, conflits, arborescence des points."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.common.config import Settings
from app.db.models import AuditLog, Synoptic, SynopticVersion
from tests.api_harness import (
    ApiServer,
    logged_in,
    make_settings,
    make_user,
    running_api,
    seed_points,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(db_engine: AsyncEngine, redis: Any, sessions: async_sessionmaker[AsyncSession]):  # type: ignore[no-untyped-def]
    ids = await seed_points(sessions)
    for name, role in [("vera", "viewer"), ("olivia", "operator"), ("edgar", "engineer")]:
        await make_user(sessions, name, role)
    async with running_api(db_engine, redis) as api:
        yield api, ids, sessions


def document(
    point: uuid.UUID | None = None, name: str = "CTA 1", widgets: int = 2
) -> dict[str, Any]:
    items = [
        {
            "id": f"w{i}", "type": "value", "x": 10 * i, "y": 20, "w": 160, "h": 48,
            "bind": {"point": str(point)} if point else {},
        }
        for i in range(1, widgets + 1)
    ]  # fmt: skip
    return {"schema": 1, "name": name, "canvas": {"width": 1920, "height": 1080}, "widgets": items}


async def create(api: ApiServer, doc: dict[str, Any], **extra: Any) -> dict[str, Any]:
    async with logged_in(api, "edgar") as client:
        response = await client.post("/synoptics", json={"doc": doc, **extra})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_engineer_creates_a_synoptic_that_everyone_can_read(env: Any) -> None:
    api, ids, sessions = env
    created = await create(api, document(ids["Temp soufflage"], "Salle serveurs - étage 2"))
    assert (created["slug"], created["version"], created["name"]) == (
        "salle-serveurs-etage-2",
        1,
        "Salle serveurs - étage 2",
    )
    assert created["doc"]["schema"] == 1 and len(created["doc"]["widgets"]) == 2

    for user in ("vera", "olivia", "edgar"):
        async with logged_in(api, user) as client:
            listed = (await client.get("/synoptics")).json()
            assert [(s["slug"], s["version"]) for s in listed] == [("salle-serveurs-etage-2", 1)]
            got = (await client.get("/synoptics/salle-serveurs-etage-2")).json()
            assert got["doc"] == created["doc"] and got["id"] == created["id"]
            assert (await client.get("/synoptics/inexistant")).status_code == 404

    async with sessions() as session:
        (row,) = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "synoptic.create"))
        ).all()
        assert row.target == "salle-serveurs-etage-2" and row.after == {"version": 1, "widgets": 2}


async def test_only_engineers_write_and_anonymous_users_read_nothing(env: Any) -> None:
    api, ids, _ = env
    created = await create(api, document())
    doc = document()
    for user in ("vera", "olivia"):
        async with logged_in(api, user) as client:
            assert (await client.post("/synoptics", json={"doc": doc})).status_code == 403
            assert (
                await client.put(f"/synoptics/{created['id']}", json={"doc": doc})
            ).status_code == 403
            assert (await client.delete(f"/synoptics/{created['id']}")).status_code == 403
            assert (await client.get(f"/synoptics/{created['slug']}/versions")).status_code == 403
            assert (await client.post(f"/synoptics/{created['id']}/restore/1")).status_code == 403
    async with api.client() as anonymous:
        assert (await anonymous.get("/synoptics")).status_code == 401
        assert (await anonymous.get(f"/synoptics/{created['slug']}")).status_code == 401


async def test_saving_creates_a_new_version_and_previous_ones_stay_readable(env: Any) -> None:
    api, ids, sessions = env
    created = await create(api, document(widgets=2))
    async with logged_in(api, "edgar") as client:
        second = (
            await client.put(
                f"/synoptics/{created['id']}", json={"doc": document(widgets=3), "base_version": 1}
            )
        ).json()
        third = (
            await client.put(
                f"/synoptics/{created['id']}", json={"doc": document(name="CTA 1 bis", widgets=1)}
            )
        ).json()
        assert (second["version"], third["version"]) == (2, 3) and third["name"] == "CTA 1 bis"

        versions = (await client.get(f"/synoptics/{created['slug']}/versions")).json()
        assert [(v["version"], v["created_by"]) for v in versions] == [
            (3, "edgar"),
            (2, "edgar"),
            (1, "edgar"),
        ]
        old = (await client.get(f"/synoptics/{created['slug']}/versions/1")).json()
        assert len(old["doc"]["widgets"]) == 2 and old["version"] == 1
        assert (await client.get(f"/synoptics/{created['slug']}/versions/9")).status_code == 404
        # La lecture courante donne toujours la dernière version, sous le même slug.
        current = (await client.get(f"/synoptics/{created['slug']}")).json()
        assert current["version"] == 3 and len(current["doc"]["widgets"]) == 1
    async with sessions() as session:
        assert len((await session.scalars(select(SynopticVersion))).all()) == 3


async def test_restoring_an_old_version_creates_a_new_one_and_keeps_the_history(env: Any) -> None:
    api, ids, sessions = env
    created = await create(api, document(widgets=2))
    async with logged_in(api, "edgar") as client:
        await client.put(
            f"/synoptics/{created['id']}", json={"doc": document(name="Autre nom", widgets=5)}
        )
        restored = await client.post(f"/synoptics/{created['id']}/restore/1")
        assert restored.status_code == 200, restored.text
        body = restored.json()
        assert body["version"] == 3 and body["name"] == "CTA 1"  # contenu et nom de la version 1
        assert body["doc"] == created["doc"]

        assert (await client.get(f"/synoptics/{created['slug']}")).json()["doc"] == created["doc"]
        # L'historique n'est jamais réécrit : la version 2 existe toujours.
        assert (
            len(
                (await client.get(f"/synoptics/{created['slug']}/versions/2")).json()["doc"][
                    "widgets"
                ]
            )
            == 5
        )
        assert (await client.post(f"/synoptics/{created['id']}/restore/42")).status_code == 404
        assert (await client.post(f"/synoptics/{uuid.uuid4()}/restore/1")).status_code == 404
    async with sessions() as session:
        row = (
            await session.scalars(select(AuditLog).where(AuditLog.action == "synoptic.restore"))
        ).one()
        assert row.before == {"restored_version": 1} and row.after == {"version": 3}


async def test_a_stale_editor_cannot_overwrite_a_newer_save(env: Any) -> None:
    api, ids, _ = env
    created = await create(api, document())
    async with logged_in(api, "edgar") as client:
        url = f"/synoptics/{created['id']}"
        assert (
            await client.put(url, json={"doc": document(widgets=3), "base_version": 1})
        ).status_code == 200
        conflict = await client.put(url, json={"doc": document(widgets=4), "base_version": 1})
        assert conflict.status_code == 409 and "version 2" in conflict.json()["detail"]
        assert (await client.get(f"/synoptics/{created['slug']}")).json()[
            "version"
        ] == 2  # rien d'écrasé
        assert (
            await client.put(url, json={"doc": document(widgets=4), "base_version": 2})
        ).status_code == 200


async def test_slugs_are_derived_made_unique_or_explicit(env: Any) -> None:
    api, ids, _ = env
    first = await create(api, document(name="Salle A"))
    second = await create(api, document(name="Salle A"))  # même nom : le slug s'adapte
    assert (first["slug"], second["slug"]) == ("salle-a", "salle-a-2")
    explicit = await create(api, document(name="Autre"), slug="mon-slug")
    assert explicit["slug"] == "mon-slug"
    async with logged_in(api, "edgar") as client:
        taken = await client.post("/synoptics", json={"slug": "mon-slug", "doc": document()})
        assert taken.status_code == 409
        for bad in ("Mon Slug", "a--b", "é"):
            assert (
                await client.post("/synoptics", json={"slug": bad, "doc": document()})
            ).status_code == 422


async def test_invalid_documents_are_refused_with_a_useful_message(env: Any) -> None:
    api, ids, _ = env
    created = await create(api, document())
    broken = document()
    broken["widgets"][0]["rules"] = [{"when": "value > ", "style": {"color": "red"}}]
    bad_style = document()
    bad_style["widgets"][0]["style"] = {"color": "url(https://evil/x)"}
    unknown = document()
    unknown["widgets"][0]["type"] = "hologramme"
    async with logged_in(api, "edgar") as client:
        for doc, fragment in [
            (broken, "expression de règle"),
            (bad_style, "refusée"),
            (unknown, "type de widget"),
        ]:
            response = await client.post("/synoptics", json={"doc": doc})
            assert response.status_code == 422, fragment
            assert fragment in str(response.json()["detail"])
            assert (
                await client.put(f"/synoptics/{created['id']}", json={"doc": doc})
            ).status_code == 422
        assert (await client.post("/synoptics", json={"doc": {"nom": "x"}})).status_code == 422
        assert (await client.post("/synoptics", json={})).status_code == 422
    async with logged_in(api, "olivia") as client:  # rien n'a été enregistré
        assert (await client.get("/synoptics")).json()[0]["version"] == 1


async def test_documents_over_the_size_limit_are_refused(env: Any) -> None:
    api, ids, _ = env
    huge = document()
    huge["widgets"][0]["text"] = "x" * (6 * 1024 * 1024 + 10)
    async with logged_in(api, "edgar") as client:
        response = await client.post("/synoptics", json={"doc": huge})
    assert response.status_code == 413


async def test_deleting_a_synoptic_removes_all_its_versions(env: Any) -> None:
    api, ids, sessions = env
    created = await create(api, document())
    async with logged_in(api, "edgar") as client:
        await client.put(f"/synoptics/{created['id']}", json={"doc": document(widgets=3)})
        assert (await client.delete(f"/synoptics/{created['id']}")).status_code == 204
        assert (await client.delete(f"/synoptics/{created['id']}")).status_code == 404
        assert (await client.get(f"/synoptics/{created['slug']}")).status_code == 404
        assert (await client.get("/synoptics")).json() == []
    async with sessions() as session:
        assert (await session.scalars(select(Synoptic))).all() == []
        assert (await session.scalars(select(SynopticVersion))).all() == []
        assert (
            await session.scalars(select(AuditLog).where(AuditLog.action == "synoptic.delete"))
        ).one()


async def test_bound_points_may_be_deleted_later_without_blocking_old_versions(env: Any) -> None:
    api, ids, sessions = env
    gone = uuid.uuid4()  # un point qui n'existe (plus) pas : la restauration ne doit pas échouer
    created = await create(api, document(gone))
    async with logged_in(api, "edgar") as client:
        await client.put(f"/synoptics/{created['id']}", json={"doc": document(ids["Consigne"])})
        assert (await client.post(f"/synoptics/{created['id']}/restore/1")).status_code == 200


# -- arborescence des points --------------------------------------------------------------------


async def test_points_tree_lists_folders_with_counts_then_points(env: Any) -> None:
    api, ids, _ = env
    async with logged_in(api, "vera") as client:
        root = (await client.get("/points/tree")).json()
        assert root["path"] == "" and root["points"] == []
        assert root["folders"] == [{"name": "Site", "path": "Site", "count": 4}]

        site = (await client.get("/points/tree", params={"path": "Site"})).json()
        assert [(f["name"], f["path"], f["count"]) for f in site["folders"]] == [
            ("Bat A", "Site/Bat A", 3),
            ("Bat B", "Site/Bat B", 1),
        ]

        cta = (await client.get("/points/tree", params={"path": "Site/Bat A/CTA-1"})).json()
        assert cta["folders"] == []
        assert [p["name"] for p in cta["points"]] == ["Consigne", "Temp reprise", "Temp soufflage"]
        first = next(p for p in cta["points"] if p["name"] == "Temp soufflage")
        assert (
            first["latest"]["value"] == 21.5
            and first["writable"] is False
            and first["unit"] == "°C"
        )

        assert (await client.get("/points/tree", params={"path": "Site/Bat A/"})).json()[
            "path"
        ] == "Site/Bat A"  # « / » final toléré
        assert (await client.get("/points/tree", params={"path": "Inconnu"})).json() == {
            "path": "Inconnu",
            "folders": [],
            "points": [],
        }
    async with api.client() as anonymous:
        assert (await anonymous.get("/points/tree")).status_code == 401


# -- service du frontend -----------------------------------------------------------------------


async def test_the_api_serves_the_built_frontend_without_shadowing_its_own_routes(
    db_engine: AsyncEngine, redis: Any, tmp_path: Any
) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Superviseur</title>")
    (tmp_path / "app.js").write_text("console.log(1)")
    settings: Settings = make_settings(frontend_dir=str(tmp_path))
    async with running_api(db_engine, redis, settings) as api:
        import httpx

        async with httpx.AsyncClient(base_url=api.base_url) as client:
            assert "Superviseur" in (await client.get("/")).text
            assert (await client.get("/app.js")).text == "console.log(1)"
            assert (await client.get("/health")).status_code == 200  # l'API reste prioritaire
            assert (await client.get("/api/v1/health")).status_code == 200
            assert (await client.get("/api/v1/synoptics")).status_code == 401
            assert (await client.get("/absent.js")).status_code == 404
