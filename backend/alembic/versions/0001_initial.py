"""Schéma initial (section 5.1 du cahier des charges).

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("viewer", "operator", "engineer", "admin")


def _uuid_pk() -> sa.Column[sa.Uuid]:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def _fk(
    column: str, target: str, *, ondelete: str | None = None, nullable: bool = False
) -> sa.Column[sa.Uuid]:
    return sa.Column(column, sa.Uuid(), sa.ForeignKey(target, ondelete=ondelete), nullable=nullable)


def upgrade() -> None:
    # Déjà présente dans l'image timescale/timescaledb ; sans effet dans ce cas.
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "role",
        sa.Column("id", sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.UniqueConstraint("name", name="uq_role_name"),
    )
    role_table = sa.table("role", sa.column("name", sa.String))
    op.bulk_insert(role_table, [{"name": name} for name in ROLES])

    op.create_table(
        "user",
        _uuid_pk(),
        sa.Column("login", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("role.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("login", name="uq_user_login"),
    )

    op.create_table(
        "network",
        _uuid_pk(),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("bind_ip", sa.String(64), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default=sa.text("47808")),
        sa.Column("bbmd_ip", sa.String(64), nullable=True),
        sa.Column("bbmd_ttl", sa.Integer(), nullable=True),
    )

    op.create_table(
        "device",
        _uuid_pk(),
        _fk("network_id", "network.id", ondelete="CASCADE"),
        sa.Column("instance", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(128), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("vendor", sa.String(128), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.UniqueConstraint("network_id", "instance", name="uq_device_network_id_instance"),
    )

    op.create_table(
        "point",
        _uuid_pk(),
        _fk("device_id", "device.id", ondelete="CASCADE"),
        sa.Column("object_type", sa.String(32), nullable=False),
        sa.Column("object_instance", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("writable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cov_capable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("poll_interval_s", sa.Integer(), nullable=True),
        sa.Column("path", sa.String(512), nullable=True),
        sa.Column("missing", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deadband", sa.Double(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_interval_s", sa.Integer(), nullable=False, server_default=sa.text("900")),
        sa.Column("write_min", sa.Double(), nullable=True),
        sa.Column("write_max", sa.Double(), nullable=True),
        sa.Column("state_text", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint(
            "device_id", "object_type", "object_instance", name="uq_point_device_object"
        ),
    )
    op.create_index("ix_point_path", "point", ["path"])

    op.create_table(
        "point_tag",
        sa.Column(
            "point_id",
            sa.Uuid(),
            sa.ForeignKey("point.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tag", sa.String(64), primary_key=True),
    )

    op.create_table(
        "sample",
        sa.Column(
            "point_id",
            sa.Uuid(),
            sa.ForeignKey("point.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ok'")),
    )

    op.create_table(
        "point_latest",
        sa.Column(
            "point_id",
            sa.Uuid(),
            sa.ForeignKey("point.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Double(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'ok'")),
    )

    op.create_table(
        "alarm_rule",
        _uuid_pk(),
        _fk("point_id", "point.id", ondelete="CASCADE"),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("threshold", sa.Double(), nullable=True),
        sa.Column("hysteresis", sa.Double(), nullable=False, server_default=sa.text("0")),
        sa.Column("delay_s", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column(
            "notify", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "alarm_event",
        _uuid_pk(),
        _fk("rule_id", "alarm_rule.id", ondelete="CASCADE"),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_by", sa.String(64), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
    )

    op.create_table(
        "synoptic",
        _uuid_pk(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        _fk("owner_id", "user.id", ondelete="SET NULL", nullable=True),
        sa.UniqueConstraint("slug", name="uq_synoptic_slug"),
    )

    op.create_table(
        "synoptic_version",
        _uuid_pk(),
        _fk("synoptic_id", "synoptic.id", ondelete="CASCADE"),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("json", postgresql.JSONB(), nullable=False),
        _fk("created_by", "user.id", ondelete="SET NULL", nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "synoptic_id", "version", name="uq_synoptic_version_synoptic_id_version"
        ),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _fk("user_id", "user.id", ondelete="SET NULL", nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(255), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
    )
    op.create_index("ix_audit_log_ts", "audit_log", ["ts"])


def downgrade() -> None:
    for table in (
        "audit_log",
        "synoptic_version",
        "synoptic",
        "alarm_event",
        "alarm_rule",
        "point_latest",
        "sample",
        "point_tag",
        "point",
        "device",
        "network",
        "user",
        "role",
    ):
        op.drop_table(table)
