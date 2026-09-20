"""Configuration de l'application, lue depuis les variables d'environnement."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variables d'environnement (voir `.env.example`).

    Aucune valeur par défaut ne contient de secret : les mots de passe et le
    secret JWT doivent être fournis par l'environnement.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://supervisor@localhost:5432/supervisor"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: SecretStr | None = None
    log_level: str = "INFO"
    retention_days: int = 730

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None

    bacnet_bind_ip: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Retourne la configuration (instanciée une seule fois)."""
    return Settings()
