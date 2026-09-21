"""Alarmes : nom de règle, valeur au déclenchement, une seule alarme ouverte par règle.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("alarm_rule", sa.Column("name", sa.String(255), nullable=True))
    op.add_column("alarm_event", sa.Column("raised_value", sa.Double(), nullable=True))
    op.create_index(
        "uq_alarm_event_open_rule",
        "alarm_event",
        ["rule_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'normal'"),
    )
    op.create_index("ix_alarm_event_state_raised", "alarm_event", ["state", "raised_at"])


def downgrade() -> None:
    op.drop_index("ix_alarm_event_state_raised", table_name="alarm_event")
    op.drop_index("uq_alarm_event_open_rule", table_name="alarm_event")
    op.drop_column("alarm_event", "raised_value")
    op.drop_column("alarm_rule", "name")
