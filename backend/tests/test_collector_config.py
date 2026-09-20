from pathlib import Path

import pytest

from app.collector.config import expand_targets, load_config


def test_defaults_when_file_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "absent.yaml")
    assert config.polling.batch_size == 20
    assert config.cov.lifetime_s == 3600
    assert config.network.device_instance == 599999


def test_yaml_and_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / "collector.yaml"
    file.write_text(
        "network:\n  bind_ip: 10.0.0.5/24\n  bbmd:\n    enabled: true\n"
        "    address: 10.0.0.1:47808\n"
        "polling:\n  default_interval_s: 10\n"
    )
    monkeypatch.delenv("BACNET_BIND_IP", raising=False)
    config = load_config(file)
    assert config.network.bind_ip == "10.0.0.5/24"
    assert config.network.bbmd.enabled and config.network.bbmd.ttl == 900
    assert config.polling.default_interval_s == 10

    monkeypatch.setenv("BACNET_BIND_IP", "192.168.1.10/24")
    assert load_config(file).network.bind_ip == "192.168.1.10/24"


def test_expand_targets() -> None:
    assert expand_targets(["10.0.0.7:47808", "127.0.0.1:47809-47811"]) == [
        "10.0.0.7:47808",
        "127.0.0.1:47809",
        "127.0.0.1:47810",
        "127.0.0.1:47811",
    ]
