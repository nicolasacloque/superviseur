"""Configuration du collecteur (`config/collector.yaml`, section 6.1)."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class BbmdConfig(BaseModel):
    enabled: bool = False
    address: str = ""
    ttl: int = 900


class NetworkConfig(BaseModel):
    name: str = "reseau-principal"
    bind_ip: str = "127.0.0.1/24"
    port: int = 47808
    device_instance: int = 599999
    bbmd: BbmdConfig = Field(default_factory=BbmdConfig)


class DiscoveryConfig(BaseModel):
    who_is_low: int = 0
    who_is_high: int = 4194303
    interval_s: int = 3600
    timeout_s: float = 3.0
    # Who-Is en unicast vers des adresses connues ("host:port" ou "host:port_min-port_max").
    targets: list[str] = Field(default_factory=list)
    # Objets dont le nom commence par un de ces préfixes ne sont pas importés.
    ignore_name_prefixes: list[str] = Field(default_factory=list)
    parallel_devices: int = 4


class PollingConfig(BaseModel):
    default_interval_s: float = 30.0
    batch_size: int = 20
    max_concurrent_requests: int = 5
    timeout_s: float = 5.0
    retries: int = 2
    # Nombre de cycles sans réponse avant de passer un device hors ligne.
    offline_after_cycles: int = 3


class CovConfig(BaseModel):
    enabled: bool = True
    lifetime_s: int = 3600
    renew_before_s: int = 300
    confirmed: bool = False
    # Polling de sécurité des points abonnés (détection des valeurs figées).
    safety_poll_s: float = 300.0
    # Délai avant de retenter un abonnement refusé.
    retry_s: float = 300.0


class StorageConfig(BaseModel):
    batch_rows: int = 500
    flush_s: float = 2.0


class CollectorConfig(BaseModel):
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    cov: CovConfig = Field(default_factory=CovConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


def expand_targets(targets: list[str]) -> list[str]:
    """Développe `host:47809-47818` en une adresse par port."""
    expanded: list[str] = []
    for target in targets:
        host, _, ports = target.rpartition(":")
        if "-" in ports:
            low, high = (int(p) for p in ports.split("-", 1))
            expanded.extend(f"{host}:{port}" for port in range(low, high + 1))
        else:
            expanded.append(target)
    return expanded


def load_config(path: str | Path | None = None) -> CollectorConfig:
    """Charge le YAML (défaut : `COLLECTOR_CONFIG` ou `config/collector.yaml`).

    `BACNET_BIND_IP`, si défini, remplace `network.bind_ip`.
    """
    file = Path(path or os.environ.get("COLLECTOR_CONFIG", "config/collector.yaml"))
    data = yaml.safe_load(file.read_text(encoding="utf-8")) if file.exists() else {}
    config = CollectorConfig.model_validate(data or {})
    if bind_ip := os.environ.get("BACNET_BIND_IP"):
        config.network.bind_ip = bind_ip
    return config
