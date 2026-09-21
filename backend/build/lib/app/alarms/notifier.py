"""Envoi des notifications : une par changement d'état, regroupées en cas d'avalanche (9.3)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.alarms.messages import Notification
from app.alarms.senders import Sender

log = logging.getLogger(__name__)


@dataclass
class _Job:
    kind: str
    target: str
    notifications: list[Notification]
    digest: bool = False


@dataclass
class _Digest:
    due_at: float
    items: list[Notification] = field(default_factory=list)


class Notifier:
    """File d'envoi avec anti-spam.

    Jusqu'à `threshold` notifications par fenêtre et par destinataire, chacune part seule. Au-delà,
    les suivantes s'accumulent et partent en un seul message récapitulatif quand la fenêtre est
    écoulée : l'ordre est conservé et personne n'est noyé.
    """

    def __init__(
        self,
        senders: dict[str, Sender],
        *,
        default_email: list[str] | None = None,
        threshold: int = 20,
        window_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        attempts: int = 3,
        retry_delay_s: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._senders = senders
        self._default_email = default_email or []
        self._threshold = threshold
        self._window_s = window_s
        self._clock = clock
        self._attempts = attempts
        self._retry_delay_s = retry_delay_s
        self._sleep = sleep
        self._recent: dict[tuple[str, str], deque[float]] = {}
        self._digests: dict[tuple[str, str], _Digest] = {}
        self._queue: asyncio.Queue[_Job] = asyncio.Queue()

    def targets(self, channel: str) -> list[tuple[str, str]]:
        """`email:a@b.fr` -> [("email", "a@b.fr")] ; `email` seul -> destinataires par défaut."""
        kind, _, target = channel.partition(":")
        if kind == "email" and not target:
            return [("email", address) for address in self._default_email]
        return [(kind, target)] if target else []

    async def submit(self, notification: Notification) -> None:
        now = self._clock()
        for channel in notification.channels:
            for kind, target in self.targets(channel):
                self._route(kind, target, notification, now)

    def _route(self, kind: str, target: str, notification: Notification, now: float) -> None:
        key = (kind, target)
        recent = self._recent.setdefault(key, deque())
        while recent and now - recent[0] >= self._window_s:
            recent.popleft()
        digest = self._digests.get(key)
        if digest is not None or len(recent) >= self._threshold:
            if digest is None:
                # Le récapitulatif part quand la fenêtre des notifications déjà envoyées se referme.
                due_at = (recent[0] if recent else now) + self._window_s
                digest = self._digests[key] = _Digest(due_at=due_at)
            digest.items.append(notification)
            return
        recent.append(now)
        self._queue.put_nowait(_Job(kind, target, [notification]))

    def flush_due(self) -> int:
        """Met en file les récapitulatifs dont la fenêtre est écoulée ; retourne leur nombre."""
        now = self._clock()
        due = [key for key, digest in self._digests.items() if now >= digest.due_at]
        for key in due:
            digest = self._digests.pop(key)
            self._queue.put_nowait(_Job(key[0], key[1], digest.items, digest=True))
        return len(due)

    async def _deliver(self, job: _Job) -> None:
        sender = self._senders.get(job.kind)
        if sender is None:
            log.warning(
                "canal %s non configuré : notification abandonnée (%s)", job.kind, job.target
            )
            return
        for attempt in range(1, self._attempts + 1):
            try:
                if job.digest:
                    await sender.send_digest(job.target, job.notifications, self._window_s)
                else:
                    await sender.send(job.target, job.notifications[0])
                return
            except Exception as exc:
                log.warning(
                    "envoi %s vers %s en échec (%d/%d) : %s",
                    job.kind,
                    job.target,
                    attempt,
                    self._attempts,
                    exc,
                )
                if attempt < self._attempts:
                    await self._sleep(self._retry_delay_s * 2 ** (attempt - 1))
        log.error(
            "notification abandonnée après %d essais : %s vers %s",
            self._attempts,
            job.kind,
            job.target,
        )

    async def run(self) -> None:
        """Vide la file (un envoi à la fois) et déclenche les récapitulatifs échus."""
        flusher = asyncio.create_task(self._flush_loop())
        try:
            while True:
                job = await self._queue.get()
                try:
                    await self._deliver(job)
                finally:
                    self._queue.task_done()
        finally:
            flusher.cancel()

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            self.flush_due()

    async def join(self) -> None:
        """Attend que tout ce qui est en file soit envoyé (tests, arrêt propre)."""
        await self._queue.join()
