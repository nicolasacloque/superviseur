"""Canaux de notification d'une règle : validation (modules sans dépendance réseau)."""

from __future__ import annotations

from urllib.parse import urlparse


def valid_webhook_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)
