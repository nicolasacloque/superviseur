from sqlalchemy import UniqueConstraint

from app.db.models import Base

EXPECTED_TABLES = {
    "network",
    "device",
    "point",
    "point_tag",
    "sample",
    "point_latest",
    "alarm_rule",
    "alarm_event",
    "synoptic",
    "synoptic_version",
    "user",
    "role",
    "audit_log",
}


def test_metadata_declares_all_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_device_and_point_uniqueness() -> None:
    def unique_columns(table: str) -> set[tuple[str, ...]]:
        return {
            tuple(c.name for c in constraint.columns)
            for constraint in Base.metadata.tables[table].constraints
            if isinstance(constraint, UniqueConstraint)
        }

    assert ("network_id", "instance") in unique_columns("device")
    assert ("device_id", "object_type", "object_instance") in unique_columns("point")
