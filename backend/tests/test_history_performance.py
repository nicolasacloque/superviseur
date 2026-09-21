"""Critères d'acceptation du Jalon 4 : 2000 points sur 30 jours, requête < 1 s, compression active.

Nécessite TimescaleDB (donc la CI) : la base est créée par les migrations, comme en production.
"""

import asyncio
import os
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.collector.store import DbStore
from app.db.models import Base
from tests.api_harness import logged_in, make_user, running_api
from tests.conftest import TEST_DATABASE_URL
from tests.helpers import alembic_config

pytestmark = [pytest.mark.integration, pytest.mark.perf]


def report(title: str, message: str) -> None:
    """Affiche une mesure ; sur GitHub Actions, en annotation visible sans ouvrir les journaux."""
    print(f"\n{title} : {message}")
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::notice title={title}::{message}")


POINTS = 2000
DAYS = 30
STEP = timedelta(minutes=15)  # max_interval par défaut : le minimum d'échantillons par point


@pytest.fixture
async def timescale_engine() -> AsyncIterator[AsyncEngine]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL non défini")
    assert "test" in (make_url(TEST_DATABASE_URL).database or "")
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        available = await conn.scalar(
            text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        )
        if not available:
            await engine.dispose()
            pytest.skip("TimescaleDB indisponible")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await asyncio.to_thread(command.upgrade, alembic_config(TEST_DATABASE_URL), "head")
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()


async def populate(engine: AsyncEngine, base: datetime) -> uuid.UUID:
    """2000 points, 30 jours d'échantillons toutes les 15 min ; retourne un point du milieu."""
    async with engine.begin() as conn:
        network = await conn.scalar(
            text("INSERT INTO network (name, bind_ip) VALUES ('perf', '127.0.0.1') RETURNING id")
        )
        device = await conn.scalar(
            text(
                "INSERT INTO device (network_id, instance, name, address) "
                "VALUES (:n, 1, 'perf', 'x') RETURNING id"
            ),
            {"n": network},
        )
        await conn.execute(
            text(
                "INSERT INTO point (device_id, object_type, object_instance, name) "
                "SELECT :d, 'analog-input', g, 'P' || g FROM generate_series(1, :n) g"
            ),
            {"d": device, "n": POINTS},
        )
    for day in range(DAYS):  # un jour à la fois : une seule chunk touchée par insertion
        start = base + timedelta(days=day)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO sample (point_id, ts, value, status)
                    SELECT p.id, gs,
                           20 + 5 * sin(extract(epoch FROM gs) / 3600.0 + p.object_instance),
                           'ok'
                    FROM point p
                    CROSS JOIN generate_series(
                        CAST(:start AS timestamptz),
                        CAST(:end AS timestamptz) - interval '1 second',
                        CAST(:step AS interval)
                    ) gs
                    """
                ),
                {"start": start, "end": start + timedelta(days=1), "step": STEP},
            )
    async with engine.connect() as conn:
        return uuid.UUID(
            str(
                await conn.scalar(
                    text("SELECT id FROM point WHERE object_instance = :n"), {"n": POINTS // 2}
                )
            )
        )


async def test_30_day_history_of_one_point_among_2000_answers_in_under_a_second(
    timescale_engine: AsyncEngine, redis: Any
) -> None:
    engine = timescale_engine
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    base = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=DAYS
    )
    started = time.monotonic()
    point_id = await populate(engine, base)
    report(
        "Insertion",
        f"{POINTS * DAYS * 96:,} échantillons ({POINTS} points x {DAYS} jours) en "
        f"{time.monotonic() - started:.0f} s",
    )

    # Compression des chunks de plus de 7 jours (la politique le fait chaque nuit).
    started = time.monotonic()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "SELECT compress_chunk(c, true) "
                "FROM show_chunks('sample', older_than => INTERVAL '7 days') c"
            )
        )
    report(
        "Compression", f"chunks de plus de 7 jours compressés en {time.monotonic() - started:.0f} s"
    )

    async with engine.connect() as conn:
        compressed_chunks = await conn.scalar(
            text(
                "SELECT count(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_name = 'sample' AND is_compressed"
            )
        )
        stats = (
            await conn.execute(
                text(
                    "SELECT sum(before_compression_total_bytes)::float8 AS before, "
                    "sum(after_compression_total_bytes)::float8 AS after "
                    "FROM chunk_compression_stats('sample')"
                )
            )
        ).one()
        total_chunks = await conn.scalar(
            text(
                "SELECT count(*) FROM timescaledb_information.chunks "
                "WHERE hypertable_name = 'sample'"
            )
        )
    report(
        "Compression",
        f"{compressed_chunks}/{total_chunks} chunks compressés, "
        f"ratio {stats.before / stats.after:.1f}",
    )
    assert compressed_chunks >= DAYS - 9  # tout sauf la semaine récente
    assert stats.before / stats.after > 1.5  # la compression est effective
    assert total_chunks >= DAYS

    await make_user(sessions, "vera", "viewer")
    window = {"from": base.isoformat(), "to": (base + timedelta(days=DAYS)).isoformat()}
    async with (
        running_api(engine, redis) as api,
        logged_in(api, "vera") as client,
    ):
        await client.get("/health")  # connexion du pool déjà chaude : on ne mesure que la requête
        cases: list[tuple[str, dict[str, Any], int | None]] = [
            ("brut", {}, DAYS * 96),
            ("1 heure", {"bucket": "1h"}, DAYS * 24),
            ("auto", {"bucket": "auto", "max_points": 500}, None),
        ]
        for name, params, expected in cases:
            started = time.monotonic()
            response = await client.get(f"/points/{point_id}/history", params={**window, **params})
            elapsed = time.monotonic() - started
            report(
                f"Historique 30 jours ({name})",
                f"{elapsed * 1000:.0f} ms, {len(response.json()['items'])} points",
            )
            assert response.status_code == 200, response.text
            body = response.json()
            if expected is not None:
                assert len(body["items"]) == expected
            assert body["truncated"] is False
            assert elapsed < 1.0, f"{name} : {elapsed:.2f} s"
        assert body["bucket"] == "3h"  # 30 j / 500 points -> 86 min -> tranche ronde de 3 h


async def test_history_writes_reach_at_least_2000_samples_per_second(
    timescale_engine: AsyncEngine,
) -> None:
    engine = timescale_engine
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        network = await conn.scalar(
            text("INSERT INTO network (name, bind_ip) VALUES ('w', '127.0.0.1') RETURNING id")
        )
        device = await conn.scalar(
            text(
                "INSERT INTO device (network_id, instance, name, address) "
                "VALUES (:n, 1, 'w', 'x') RETURNING id"
            ),
            {"n": network},
        )
        await conn.execute(
            text(
                "INSERT INTO point (device_id, object_type, object_instance, name) "
                "SELECT :d, 'analog-input', g, 'P' || g FROM generate_series(1, 2000) g"
            ),
            {"d": device},
        )
        ids = [row[0] for row in await conn.execute(text("SELECT id FROM point"))]

    now = datetime.now(UTC)
    samples = [
        {
            "point_id": point_id,
            "ts": now + timedelta(seconds=second),
            "value": float(second),
            "status": "ok",
        }
        for second in range(10)
        for point_id in ids
    ]  # 20 000 échantillons
    latest = [{"point_id": point_id, "ts": now, "value": 1.0, "status": "ok"} for point_id in ids]
    started = time.monotonic()
    await DbStore(sessions).write_samples(samples, latest)
    rate = len(samples) / (time.monotonic() - started)
    report("Écriture de l'historique", f"{rate:,.0f} échantillons/s (cible : 2 000)")
    assert rate >= 2000
