"""Migrations Alembic contre une vraie base TimescaleDB.

Ignoré si `TEST_DATABASE_URL` n'est pas défini. Le nom de la base doit contenir
« test » : les tests suppriment les tables de l'application.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.models import Base
from app.db.timescale import sync_retention
from tests.helpers import alembic_config

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL non défini")


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert TEST_DATABASE_URL is not None
    database = make_url(TEST_DATABASE_URL).database or ""
    assert "test" in database, "TEST_DATABASE_URL doit cibler une base de test"

    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)  # tables laissées par d'autres tests
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    yield engine
    await engine.dispose()


async def _upgrade() -> None:
    assert TEST_DATABASE_URL is not None
    await asyncio.to_thread(command.upgrade, alembic_config(TEST_DATABASE_URL), "head")


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
    await asyncio.to_thread(command.downgrade, alembic_config(TEST_DATABASE_URL), "base")

    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))

    assert tables <= {"alembic_version"}


async def test_sample_is_a_compressed_hypertable_with_policies(engine: AsyncEngine) -> None:
    await _upgrade()
    async with engine.connect() as conn:
        compressed = await conn.scalar(
            text(
                "SELECT compression_enabled FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'sample'"
            )
        )
        chunk_interval = await conn.scalar(
            text(
                "SELECT time_interval FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = 'sample' AND column_name = 'ts'"
            )
        )
        segment_by = await conn.scalar(
            text(
                "SELECT segmentby_column_index IS NOT NULL "
                "FROM timescaledb_information.compression_settings "
                "WHERE hypertable_name = 'sample' AND attname = 'point_id'"
            )
        )
        jobs = {
            row.proc_name: row.config
            for row in await conn.execute(
                text(
                    "SELECT proc_name, config FROM timescaledb_information.jobs "
                    "WHERE hypertable_name = 'sample'"
                )
            )
        }
    assert compressed is True
    assert chunk_interval == timedelta(days=1)
    assert segment_by is True  # un segment par point : lecture d'un point sans tout décompresser
    configs = {
        name: (cfg if isinstance(cfg, dict) else json.loads(cfg)) for name, cfg in jobs.items()
    }
    assert configs["policy_compression"]["compress_after"] == "7 days"
    assert configs["policy_retention"]["drop_after"] == "730 days"


async def test_retention_follows_the_configured_days(engine: AsyncEngine) -> None:
    await _upgrade()

    async def drop_after() -> str | None:
        async with engine.connect() as conn:
            config = await conn.scalar(
                text(
                    "SELECT config FROM timescaledb_information.jobs "
                    "WHERE hypertable_name = 'sample' AND proc_name = 'policy_retention'"
                )
            )
        if config is None:
            return None
        return str((config if isinstance(config, dict) else json.loads(config))["drop_after"])

    await sync_retention(engine, 365)
    assert await drop_after() == "365 days"
    await sync_retention(engine, 0)  # 0 : conservation illimitée
    assert await drop_after() is None
    await sync_retention(engine, 730)
    assert await drop_after() == "730 days"


async def test_only_one_alarm_can_be_open_per_rule(engine: AsyncEngine) -> None:
    await _upgrade()
    async with engine.begin() as conn:
        network = await conn.scalar(
            text("INSERT INTO network (name, bind_ip) VALUES ('n', '127.0.0.1') RETURNING id")
        )
        device = await conn.scalar(
            text(
                "INSERT INTO device (network_id, instance, name, address) "
                "VALUES (:n, 1, 'd', 'a') RETURNING id"
            ),
            {"n": network},
        )
        point = await conn.scalar(
            text(
                "INSERT INTO point (device_id, object_type, object_instance, name) "
                "VALUES (:d, 'analog-input', 1, 'p') RETURNING id"
            ),
            {"d": device},
        )
        rule = await conn.scalar(
            text(
                "INSERT INTO alarm_rule (point_id, kind, severity, threshold) "
                "VALUES (:p, 'high', 'critical', 1) RETURNING id"
            ),
            {"p": point},
        )
    insert = text("INSERT INTO alarm_event (rule_id, raised_at, state) VALUES (:r, now(), :s)")
    async with engine.begin() as conn:
        await conn.execute(insert, {"r": rule, "s": "active_unacked"})
        await conn.execute(insert, {"r": rule, "s": "normal"})  # les alarmes closes s'accumulent
        await conn.execute(insert, {"r": rule, "s": "normal"})
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(insert, {"r": rule, "s": "cleared_unacked"})  # une seconde ouverte
