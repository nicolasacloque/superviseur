"""Noms des canaux et streams Redis (section 7 du cahier des charges)."""

from __future__ import annotations

import uuid

STREAM_WRITE = "cmd.write"
WRITE_GROUP = "collector"
CHANNEL_DEVICE_STATUS = "device.status"
CHANNEL_ALARM_EVENT = "alarm.event"
# Réglage d'un point modifié (deadband, intervalles) : le collecteur le recharge à chaud.
CHANNEL_POINT_CONFIG = "point.config"


def point_value_channel(point_id: uuid.UUID | str) -> str:
    return f"point.value.{point_id}"


def write_result_channel(command_id: str) -> str:
    return f"cmd.result.{command_id}"
