"""Service alarms : `python -m app.alarms.main`."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from app.alarms.commands import AlarmCommands
from app.alarms.engine import AlarmEngine
from app.alarms.notifier import Notifier
from app.alarms.runner import EngineRunner
from app.alarms.senders import EmailSender, Sender, WebhookSender
from app.alarms.store import DbAlarmStore
from app.common.config import Settings, get_settings
from app.common.logging import configure_logging
from app.db.session import create_engine, create_session_factory

log = logging.getLogger(__name__)

# Fichier touché régulièrement, lu par le healthcheck Docker.
HEARTBEAT_FILE = Path("/tmp/alarms.alive")
RELOAD_PERIOD_S = 60.0


def build_notifier(settings: Settings) -> Notifier:
    senders: dict[str, Sender] = {"webhook": WebhookSender()}
    if settings.smtp_host:
        senders["email"] = EmailSender(
            settings.smtp_host,
            settings.smtp_port,
            settings.alarm_email_from,
            settings.smtp_user,
            settings.smtp_password.get_secret_value() if settings.smtp_password else None,
            settings.smtp_starttls,
        )
    default_email = [a.strip() for a in settings.alarm_email_to.split(",") if a.strip()]
    return Notifier(
        senders,
        default_email=default_email,
        threshold=settings.alarm_digest_threshold,
        window_s=settings.alarm_digest_window_s,
    )


async def _every(period_s: float, action: Any) -> None:
    while True:
        await asyncio.sleep(period_s)
        try:
            await action()
        except Exception:
            log.exception("tâche périodique en échec")


async def _heartbeat() -> None:
    while True:
        await asyncio.to_thread(HEARTBEAT_FILE.touch)
        await asyncio.sleep(10)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    db = create_engine(settings.database_url)
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    store = DbAlarmStore(create_session_factory(db))
    notifier = build_notifier(settings)

    async def publish(channel: str, payload: dict[str, Any]) -> None:
        await redis.publish(channel, json.dumps(payload))

    engine = AlarmEngine(store, publish, notifier)
    await engine.load()
    jobs = [
        _heartbeat(),
        notifier.run(),
        EngineRunner(engine, redis).run(),
        AlarmCommands(engine, store, redis).run(),
        _every(1.0, engine.tick),
        _every(RELOAD_PERIOD_S, engine.load),
    ]
    tasks = [asyncio.create_task(job) for job in jobs]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("moteur d'alarmes démarré")
    try:
        # Une tâche qui plante arrête le service : Docker le redémarre.
        waiter = asyncio.create_task(stop.wait())
        await asyncio.wait([waiter, *tasks], return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            if task.done() and not task.cancelled() and task.exception():
                log.error("tâche arrêtée : %r", task.exception())
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await redis.aclose()
        await db.dispose()
        log.info("moteur d'alarmes arrêté")


def main() -> None:
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())


if __name__ == "__main__":
    main()
