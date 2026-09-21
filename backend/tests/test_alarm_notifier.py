"""Notifications : une par changement d'état, canaux, regroupement anti-avalanche, reprises."""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.alarms.channels import valid_webhook_url
from app.alarms.messages import AlarmInfo, Notification, build_notification, digest_text
from app.alarms.notifier import Notifier
from app.alarms.senders import WebhookSender

T0 = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class RecordingSender:
    def __init__(self, failures: int = 0) -> None:
        self.sent: list[tuple[str, Notification]] = []
        self.digests: list[tuple[str, list[Notification]]] = []
        self.failures = failures
        self.attempts = 0

    async def send(self, target: str, notification: Notification) -> None:
        self.attempts += 1
        if self.failures:
            self.failures -= 1
            raise OSError("smtp injoignable")
        self.sent.append((target, notification))

    async def send_digest(
        self, target: str, notifications: list[Notification], window_s: float
    ) -> None:
        self.digests.append((target, list(notifications)))


def notification(index: int = 0, channels: tuple[str, ...] = ("email:ops@x.fr",)) -> Notification:
    return Notification(
        event_id=f"e{index}", kind="raised", severity="critical", subject=f"alarme {index}",
        body="corps", payload={"id": f"e{index}"}, channels=channels,
    )  # fmt: skip


async def no_sleep(_: float) -> None:
    return None


def make(sender: RecordingSender | None = None, clock: Clock | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
    sender = sender or RecordingSender()
    clock = clock or Clock()
    kwargs.setdefault("sleep", no_sleep)
    return Notifier({"email": sender, "webhook": sender}, clock=clock, **kwargs), sender, clock


async def drain(notifier: Notifier) -> None:
    worker = asyncio.create_task(notifier.run())
    try:
        await notifier.join()
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


async def test_each_state_change_is_sent_exactly_once_per_recipient() -> None:
    notifier, sender, _ = make()
    await notifier.submit(notification(1, ("email:ops@x.fr", "webhook:https://hook.example/a")))
    await drain(notifier)
    assert sorted(t for t, _ in sender.sent) == ["https://hook.example/a", "ops@x.fr"]
    assert len(sender.sent) == 2  # ni doublon, ni canal oublié


async def test_the_default_recipients_are_used_for_a_bare_email_channel() -> None:
    notifier, sender, _ = make(default_email=["a@x.fr", "b@x.fr"])
    await notifier.submit(notification(1, ("email",)))
    await drain(notifier)
    assert sorted(t for t, _ in sender.sent) == ["a@x.fr", "b@x.fr"]


async def test_a_bare_email_channel_without_default_recipient_sends_nothing() -> None:
    notifier, sender, _ = make()
    await notifier.submit(notification(1, ("email",)))
    await drain(notifier)
    assert sender.sent == []


async def test_more_than_20_alarms_in_a_minute_are_grouped_into_one_message() -> None:
    notifier, sender, clock = make(threshold=20, window_s=60)
    for i in range(25):
        clock.now = i  # 25 notifications en 25 secondes
        await notifier.submit(notification(i))
    await drain(notifier)
    assert len(sender.sent) == 20  # les 20 premières partent seules
    assert sender.digests == []  # les 5 suivantes attendent la fin de la fenêtre

    clock.now = 61
    assert notifier.flush_due() == 1
    await drain(notifier)
    assert len(sender.digests) == 1
    target, grouped = sender.digests[0]
    assert target == "ops@x.fr" and [n.event_id for n in grouped] == [
        f"e{i}" for i in range(20, 25)
    ]
    assert len(sender.sent) == 20  # aucune n'est partie en double


async def test_the_window_slides_and_normal_service_resumes_after_a_digest() -> None:
    notifier, sender, clock = make(threshold=2, window_s=60)
    for i in range(3):
        await notifier.submit(notification(i))
    clock.now = 61
    notifier.flush_due()
    await notifier.submit(notification(99))  # fenêtre écoulée : de nouveau envoyée seule
    await drain(notifier)
    assert [n.event_id for _, n in sender.sent] == ["e0", "e1", "e99"]
    assert len(sender.digests) == 1 and len(sender.digests[0][1]) == 1


async def test_grouping_is_per_recipient() -> None:
    notifier, sender, _ = make(threshold=1)
    await notifier.submit(notification(1, ("email:a@x.fr",)))
    await notifier.submit(notification(2, ("email:b@x.fr",)))  # autre destinataire : pas regroupé
    await drain(notifier)
    assert sorted(t for t, _ in sender.sent) == ["a@x.fr", "b@x.fr"]


async def test_a_failed_delivery_is_retried_then_succeeds() -> None:
    notifier, sender, _ = make(RecordingSender(failures=2), attempts=3)
    await notifier.submit(notification(1))
    await drain(notifier)
    assert sender.attempts == 3 and len(sender.sent) == 1  # une seule notification livrée


async def test_a_permanently_failing_channel_is_abandoned_without_blocking_the_others() -> None:
    notifier, sender, _ = make(RecordingSender(failures=99), attempts=2)
    await notifier.submit(notification(1))
    await drain(notifier)
    assert sender.attempts == 2 and sender.sent == []
    healthy = RecordingSender()
    notifier2 = Notifier({"email": healthy}, sleep=no_sleep)
    await notifier2.submit(notification(2))
    await drain(notifier2)
    assert len(healthy.sent) == 1


async def test_a_channel_without_sender_is_dropped() -> None:
    notifier = Notifier({}, sleep=no_sleep)  # SMTP non configuré
    await notifier.submit(notification(1))
    await drain(notifier)  # ne bloque pas, ne lève pas


def info(**overrides: Any) -> AlarmInfo:
    values: dict[str, Any] = dict(
        event_id=uuid.uuid4(), rule_id=uuid.uuid4(), rule_name="Température haute", kind="high",
        severity="critical", state="active_unacked", point_id=uuid.uuid4(), point_name="Temp",
        path="Site/CTA-1/Temp soufflage", unit="°C", threshold=28.0, value=30.5, raised_at=T0,
    )  # fmt: skip
    return AlarmInfo(**{**values, **overrides})


def test_messages_carry_what_an_operator_needs() -> None:
    n = build_notification(info(), "raised", ["email"])
    assert n.subject == "[CRITIQUE] Température haute - Site/CTA-1/Temp soufflage : déclenchée"
    for expected in (
        "Valeur : 30.5 °C",
        "Seuil : 28 °C",
        "Sévérité : CRITIQUE",
        "Déclenchée le : 2026-09-21T10:00:00+00:00",
    ):
        assert expected in n.body
    assert n.payload["state"] == "active_unacked" and n.payload["value"] == 30.5

    acked = build_notification(
        info(acked_at=T0, acked_by="olivia", state="active_acked"), "acked", []
    )
    assert "acquittée" in acked.subject and "par olivia" in acked.body


def test_digest_text_caps_the_listing() -> None:
    subject, body = digest_text([notification(i) for i in range(60)], 60, limit=50)
    assert "60 changements" in subject and body.count("\n- ") == 50 and "et 10 autres" in body


@pytest.mark.parametrize(
    ("url", "valid"),
    [
        ("https://hooks.example/x", True),
        ("http://localhost:8080/h", True),
        ("ftp://x/y", False),
        ("javascript:alert(1)", False),
        ("https://", False),
    ],
)
def test_webhook_urls_must_be_http(url: str, valid: bool) -> None:
    assert valid_webhook_url(url) is valid


async def test_webhook_posts_json_and_reports_http_errors() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500 if request.url.path == "/fail" else 204)

    sender = WebhookSender(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await sender.send("https://hook.example/ok", notification(1))
    body = requests[0].read().decode()
    assert '"event":"alarm.raised"' in body.replace(" ", "") and '"alarm"' in body
    with pytest.raises(httpx.HTTPStatusError):
        await sender.send("https://hook.example/fail", notification(2))
    with pytest.raises(ValueError):
        await sender.send("file:///etc/passwd", notification(3))
    await sender.send_digest("https://hook.example/ok", [notification(4), notification(5)], 60)
    assert '"count":2' in requests[-1].read().decode().replace(" ", "")
    await sender.aclose()
