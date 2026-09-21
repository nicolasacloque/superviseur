"""Canaux de notification : email (SMTP) et webhook HTTP (JSON)."""

from __future__ import annotations

import asyncio
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any, Protocol

import httpx

from app.alarms.channels import valid_webhook_url
from app.alarms.messages import Notification, digest_text


class Sender(Protocol):
    async def send(self, target: str, notification: Notification) -> None: ...

    async def send_digest(
        self, target: str, notifications: list[Notification], window_s: float
    ) -> None: ...


class EmailSender:
    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        user: str | None = None,
        password: str | None = None,
        starttls: bool = True,
    ) -> None:
        self._host, self._port, self._sender = host, port, sender
        self._user, self._password, self._starttls = user, password, starttls

    def _message(self, target: str, subject: str, body: str) -> EmailMessage:
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self._sender, target, subject
        message.set_content(body)
        return message

    def _deliver(self, message: EmailMessage) -> None:
        if self._port == 465:
            client: smtplib.SMTP = smtplib.SMTP_SSL(self._host, self._port, timeout=15)
        else:
            client = smtplib.SMTP(self._host, self._port, timeout=15)
        with client:
            if self._starttls and self._port != 465:
                client.starttls()
            if self._user:
                client.login(self._user, self._password or "")
            client.send_message(message)

    async def send(self, target: str, notification: Notification) -> None:
        message = self._message(target, notification.subject, notification.body)
        await asyncio.to_thread(self._deliver, message)

    async def send_digest(
        self, target: str, notifications: list[Notification], window_s: float
    ) -> None:
        subject, body = digest_text(notifications, window_s)
        await asyncio.to_thread(self._deliver, self._message(target, subject, body))


class WebhookSender:
    """POST JSON vers l'URL du canal. Les redirections ne sont pas suivies."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=10.0, follow_redirects=False)

    async def _post(self, url: str, body: dict[str, Any]) -> None:
        if not valid_webhook_url(url):
            raise ValueError(f"URL de webhook invalide : {url}")
        response = await self._client.post(url, json=body)
        response.raise_for_status()

    async def send(self, target: str, notification: Notification) -> None:
        await self._post(
            target,
            {
                "event": f"alarm.{notification.kind}",
                "sent_at": datetime.now(UTC).isoformat(),
                "alarm": notification.payload,
            },
        )

    async def send_digest(
        self, target: str, notifications: list[Notification], window_s: float
    ) -> None:
        await self._post(
            target,
            {
                "event": "alarm.digest",
                "sent_at": datetime.now(UTC).isoformat(),
                "window_s": window_s,
                "count": len(notifications),
                "alarms": [{"transition": n.kind, **n.payload} for n in notifications[:200]],
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()
