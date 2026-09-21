"""Noms des canaux et streams Redis (section 7 du cahier des charges)."""

from __future__ import annotations

import uuid

STREAM_WRITE = "cmd.write"
WRITE_GROUP = "collector"
CHANNEL_DEVICE_STATUS = "device.status"
CHANNEL_ALARM_EVENT = "alarm.event"
# Réglage d'un point modifié (deadband, intervalles) : le collecteur le recharge à chaud.
CHANNEL_POINT_CONFIG = "point.config"
# Notification d'événement BACnet (Event Notification) reçue par le collecteur.
CHANNEL_BACNET_EVENT = "bacnet.event"
# Règles d'alarme créées, modifiées ou supprimées : le moteur les recharge.
CHANNEL_ALARM_RULES = "alarm.rules"
# Commandes d'acquittement (stream avec accusé de traitement, comme les écritures).
STREAM_ALARM_CMD = "cmd.alarm"
ALARM_GROUP = "alarms"


def point_value_channel(point_id: uuid.UUID | str) -> str:
    return f"point.value.{point_id}"


def write_result_channel(command_id: str) -> str:
    return f"cmd.result.{command_id}"


def alarm_result_channel(command_id: str) -> str:
    return f"alarm.result.{command_id}"
