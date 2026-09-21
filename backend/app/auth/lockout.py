"""Verrouillage temporaire des comptes après des échecs de connexion répétés (section 11).

Les compteurs vivent dans Redis (avec expiration) : pas de migration, et ils survivent au
redémarrage de l'API grâce à l'AOF de Redis. La clé est le login saisi, qu'il existe ou non :
un compte inexistant se verrouille comme un compte réel, on ne révèle donc rien.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

PREFIX = "auth"


def _fail_key(login: str) -> str:
    return f"{PREFIX}.fail.{login.strip().lower()}"


def _lock_key(login: str) -> str:
    return f"{PREFIX}.lock.{login.strip().lower()}"


class LoginGuard:
    """`max_failures` échecs en `window_s` secondes verrouillent le login pendant `lock_s` secondes.

    Si Redis est injoignable, la protection est suspendue (erreur journalisée) plutôt que de
    bloquer toute connexion : sans Redis, la supervision temps réel est de toute façon arrêtée.
    """

    def __init__(self, redis: Any, *, max_failures: int, window_s: int, lock_s: int) -> None:
        self._redis = redis
        self._max = max_failures
        self._window = window_s
        self._lock = lock_s

    async def locked_for(self, login: str) -> int:
        """Secondes restantes avant la fin du verrouillage (0 si le compte n'est pas verrouillé)."""
        try:
            remaining = await self._redis.ttl(_lock_key(login))
        except Exception:
            log.exception("verrouillage : Redis injoignable, contrôle ignoré")
            return 0
        return max(int(remaining), 0) if remaining is not None and remaining > 0 else 0

    async def record_failure(self, login: str) -> int:
        """Compte un échec ; retourne la durée du verrou si cet échec le déclenche, sinon 0."""
        try:
            key = _fail_key(login)
            count = int(await self._redis.incr(key))
            if count == 1:
                await self._redis.expire(key, self._window)
            if count < self._max:
                return 0
            await self._redis.set(_lock_key(login), "1", ex=self._lock)
            await self._redis.delete(key)
        except Exception:
            log.exception("verrouillage : Redis injoignable, échec non compté")
            return 0
        return self._lock

    async def record_success(self, login: str) -> None:
        try:
            await self._redis.delete(_fail_key(login))
        except Exception:
            log.exception("verrouillage : Redis injoignable")

    async def unlock(self, login: str) -> bool:
        """Déverrouille (action d'un administrateur) ; vrai si un verrou existait."""
        removed = await self._redis.delete(_lock_key(login), _fail_key(login))
        return bool(removed)
