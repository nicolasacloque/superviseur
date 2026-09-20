"""Migrations Alembic contre une vraie base TimescaleDB.

Ignoré si `TEST_DATABASE_URL` n'est pas défini. Le nom de la base doit contenir
« test » : les tests suppriment les tables de l'application.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.models import Base

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_DIR = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL non défini")


def _alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert TEST_DATABASE_URL is not None
    database = make_url(TEST_DATABASE_URL).database or ""
    assert "test" in database, "TEST_DATABASE_URL doit cibler une base de test"

    engine = create_async_engine(TEST_DATABASE_URL)
    config = _alembic_config(TEST_DATABASE_URL)
    # env.py lance sa propre boucle asyncio : on l'exécute dans un thread.
    await asyncio.to_thread(command.downgrade, config, "base")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)  # tables laissées par d'autres tests
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    yield engine
    await engine.dispose()


async def _upgrade() -> None:
    assert TEST_DATABASE_URL is not None
    await asyncio.to_thread(command.upgrade, _alembic_config(TEST_DATABASE_URL), "head")


async def test_upgrade_creates_schema_and_seeds_roles(engine: AsyncEngine) -> None:
    await _upgrade()

    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
        roles = (await conn.execute(text("SELECT name FROM role ORDER BY id"))).scalars().all()
        extensions = (await conn.execute(text("SELECT extname FROM pg_extension"))).scalars().all()

    assert set(Base.metadata.tables) <= tables
    assert roles == ["viewer", "operator", "engineer", "admin"]
    assert "timescaledb" in extensions


async def test_models_match_migrations(engine: AsyncEngine) -> None:
    await _upgrade()

    def diff(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as conn:
        differences = await conn.run_sync(diff)

    assert differences == []


async def test_downgrade_removes_all_tables(engine: AsyncEngine) -> None:
    assert TEST_DATABASE_URL is not None
    await _upgrade()
    await asyncio.to_thread(command.downgrade, _alembic_config(TEST_DATABASE_URL), "base")

    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))

    assert tables <= {"alembic_version"}
