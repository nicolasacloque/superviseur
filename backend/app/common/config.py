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

    # Notifications d'alarmes (section 9.3).
    smtp_starttls: bool = True
    alarm_email_from: str = "supervision@localhost"
    # Destinataires du canal `email` sans adresse précise, séparés par des virgules.
    alarm_email_to: str = ""
    # Au-delà de N notifications par fenêtre, les suivantes sont regroupées en un seul message.
    alarm_digest_threshold: int = 20
    alarm_digest_window_s: float = 60.0

    # Authentification et écriture (section 11).
    cookie_secure: bool = True
    # Verrouillage temporaire d'un login : `login_max_failures` échecs en `login_window_s` secondes
    # le bloquent pendant `login_lock_s` secondes.
    login_max_failures: int = 5
    login_window_s: int = 900
    login_lock_s: int = 900
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    # Délai d'attente de la réponse du collecteur à une écriture de consigne.
    write_timeout_s: float = 10.0
    # Frontend compilé : s'il existe, l'API le sert à la racine (sinon Nginx s'en charge).
    frontend_dir: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Retourne la configuration (instanciée une seule fois)."""
    return Settings()
