"""Compteurs de verrouillage (Redis simulé) : seuil, fenêtre, déverrouillage, Redis en panne."""

from typing import Any

from app.auth.lockout import LoginGuard


def guard(redis: Any, **kw: int) -> LoginGuard:
    return LoginGuard(redis, max_failures=3, window_s=60, lock_s=120, **kw)


async def test_lock_starts_at_the_threshold(redis: Any) -> None:
    g = guard(redis)
    assert await g.record_failure("alice") == 0
    assert await g.record_failure("alice") == 0
    assert await g.locked_for("alice") == 0
    assert await g.record_failure("alice") == 120
    assert 0 < await g.locked_for("alice") <= 120
    assert await g.locked_for("bob") == 0


async def test_success_and_unlock_clear_the_state(redis: Any) -> None:
    g = guard(redis)
    await g.record_failure("alice")
    await g.record_failure("alice")
    await g.record_success("alice")
    assert await g.record_failure("alice") == 0  # le compteur est reparti de zéro
    for _ in range(2):
        await g.record_failure("alice")
    assert await g.locked_for("alice") > 0
    assert await g.unlock("alice") is True
    assert await g.locked_for("alice") == 0
    assert await g.unlock("alice") is False


async def test_the_failure_window_expires(redis: Any) -> None:
    g = guard(redis)
    await g.record_failure("alice")
    await redis.expire("auth.fail.alice", 1)
    await redis.delete("auth.fail.alice")  # équivalent d'une fenêtre écoulée
    await g.record_failure("alice")
    await g.record_failure("alice")
    assert await g.locked_for("alice") == 0


class BrokenRedis:
    async def ttl(self, *a: Any) -> int:
        raise ConnectionError

    async def incr(self, *a: Any) -> int:
        raise ConnectionError

    async def delete(self, *a: Any) -> int:
        raise ConnectionError


async def test_redis_outage_suspends_the_protection_instead_of_blocking_logins() -> None:
    g = guard(BrokenRedis())
    assert await g.locked_for("alice") == 0
    assert await g.record_failure("alice") == 0
    await g.record_success("alice")
