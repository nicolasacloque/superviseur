import pytest

from app.common.config import Settings


def test_defaults_contain_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "JWT_SECRET", "SMTP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.jwt_secret is None
    assert settings.smtp_password is None
    assert "password" not in settings.database_url
    assert settings.database_url.startswith("postgresql+asyncpg://supervisor@")


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("RETENTION_DAYS", "30")
    monkeypatch.setenv("JWT_SECRET", "s3cret")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/x"
    assert settings.retention_days == 30
    assert settings.jwt_secret is not None
    assert settings.jwt_secret.get_secret_value() == "s3cret"
    assert "s3cret" not in repr(settings)
