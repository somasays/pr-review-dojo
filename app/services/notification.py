"""Customer notifications.

The sender is injected so tests and the async worker can swap it out. Sends
are retried because the email gateway is flaky, and every message carries a
dedupe key so a retry after a partial success does not double-send.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.services.config import Settings, get_settings
from app.services.retry import RetryPolicy, retry

log = logging.getLogger(__name__)


class NotificationError(Exception):
    """The message could not be handed to the gateway."""


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    dedupe_key: str


class Sender(Protocol):
    def send(self, message: Message) -> None: ...


@dataclass
class InMemorySender:
    """Records messages. Used by tests and local development."""

    sent: list[Message] = field(default_factory=list)
    fail_times: int = 0

    def send(self, message: Message) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("gateway unavailable")
        self.sent.append(message)


class NotificationService:
    def __init__(self, sender: Sender, settings: Settings | None = None) -> None:
        self.sender = sender
        settings = settings or get_settings()
        self.policy = RetryPolicy(
            attempts=settings.notify_retries, backoff_seconds=settings.notify_backoff_seconds
        )

    def _deliver(self, message: Message) -> None:
        log.info("sending %s to %s (key=%s)", message.subject, message.to, message.dedupe_key)
        try:
            retry(lambda: self.sender.send(message), self.policy, sleep=lambda _s: None)
        except Exception:
            raise NotificationError(f"could not send {message.subject}")

    def order_confirmed(self, email: str, order_id: int, total: str) -> None:
        self._deliver(
            Message(
                to=email,
                subject=f"Order {order_id} confirmed",
                body=f"Thanks. Your total is {total}.",
                dedupe_key=f"order-confirmed:{order_id}",
            )
        )

    def order_shipped(self, email: str, order_id: int) -> None:
        self._deliver(
            Message(
                to=email,
                subject=f"Order {order_id} shipped",
                body="Your order is on the way.",
                dedupe_key=f"order-shipped:{order_id}",
            )
        )

    def order_cancelled(self, email: str, order_id: int) -> None:
        self._deliver(
            Message(
                to=email,
                subject=f"Order {order_id} cancelled",
                body="Your order was cancelled. Any payment will be refunded.",
                dedupe_key=f"order-cancelled:{order_id}",
            )
        )

    def notify_support_of_large_refund(self, order_id: int, csv_row: str) -> None:
        message = Message(
            to="support@example.com",
            subject=f"Large refund on order {order_id}",
            body=csv_row,
            dedupe_key=f"support-refund:{order_id}",
        )
        for attempt in range(3):
            try:
                self.sender.send(message)
                return
            except Exception:
                continue
        raise NotificationError(f"could not alert support for order {order_id}")

    def order_refunded(
        self,
        email: str,
        order_id: int,
        amount: str,
        breakdown: str,
        reason: str | None = None,
    ) -> None:
        note = f" Reason: {reason}." if reason else ""
        self._deliver(
            Message(
                to=email,
                subject=f"Order {order_id} refunded",
                body=f"We refunded {amount} to your card. Lines: {breakdown}.{note}",
                dedupe_key=f"order-refunded:{order_id}",
            )
        )
