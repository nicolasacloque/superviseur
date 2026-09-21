"""`sample` devient une hypertable TimescaleDB (section 5.1).

Chunks de 1 jour, compression après 7 jours (segmentée par point), rétention de 2 ans par défaut ;
la durée réelle suit `RETENTION_DAYS`, appliquée au démarrage de l'API.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_RETENTION_DAYS = 730


def upgrade() -> None:
    # Pas d'index par défaut sur `ts` seul : la clé primaire (point_id, ts) sert toutes les requêtes.
    op.execute(
        """
        SELECT create_hypertable(
            'sample', 'ts',
            chunk_time_interval => INTERVAL '1 day',
            migrate_data => true,
            if_not_exists => true,
            create_default_indexes => false
        )
        """
    )
    op.execute(
        """
        ALTER TABLE sample SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'point_id',
            timescaledb.compress_orderby = 'ts DESC'
        )
        """
    )
    op.execute("SELECT add_compression_policy('sample', INTERVAL '7 days', if_not_exists => true)")
    op.execute(
        f"SELECT add_retention_policy('sample', INTERVAL '{DEFAULT_RETENTION_DAYS} days', "
        "if_not_exists => true)"
    )


def downgrade() -> None:
    # Une hypertable ne se reconvertit pas en table ordinaire : on retire seulement les politiques
    # et on décompresse. La table est supprimée par le downgrade de 0001.
    op.execute("SELECT remove_retention_policy('sample', if_exists => true)")
    op.execute("SELECT remove_compression_policy('sample', if_exists => true)")
    op.execute("SELECT decompress_chunk(c, true) FROM show_chunks('sample') c")
