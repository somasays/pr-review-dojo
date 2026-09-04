"""Customer notifications.

The sender is injected so tests and the async worker can swap it out. Sends
are retried because the email gateway is flaky, and every message carries a
dedupe key so a retry after a partial success does not double-send.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.services.config import Settings, get_settings
from app.services.retry import RetryExhausted, RetryPolicy, retry

log = logging.getLogger(__name__)


class NotificationError(Exception):
    """A message could not be handed to the gateway."""


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    dedupe_key: str


class Sender(Protocol):
    def send(self, message: Message) -> None: ...


def format_shipped_body(tracking_number: str | None) -> str:
    if tracking_number:
        return f"Your order is on the way. Tracking number: {tracking_number}."
    return "Your order is on the way."


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
        self.warehouse_email = settings.warehouse_email
        self.policy = RetryPolicy(
            attempts=settings.notify_retries, backoff_seconds=settings.notify_backoff_seconds
        )

    def _deliver(self, message: Message) -> None:
        log.info("sending %s to %s (key=%s)", message.subject, message.to, message.dedupe_key)
        try:
            retry(lambda: self.sender.send(message), self.policy, sleep=lambda _s: None)
        except RetryExhausted as exc:
            raise NotificationError(f"could not send {message.subject}") from exc

    def send_many(self, messages: list[Message]) -> None:
        """Hand a batch of messages to the gateway."""
        for message in messages:
            self._deliver(message)

    def order_confirmed(self, email: str, order_id: int, total: str) -> None:
        self._deliver(
            Message(
                to=email,
                subject=f"Order {order_id} confirmed",
                body=f"Thanks. Your total is {total}.",
                dedupe_key=f"order-confirmed:{order_id}",
            )
        )

    def order_shipped(self, email: str, order_id: int, tracking_number: str | None = None) -> None:
        self._deliver(
            Message(
                to=email,
                subject=f"Order {order_id} shipped",
                body=format_shipped_body(tracking_number),
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

    def warehouse_digest(self, order_ids: Sequence[int]) -> None:
        """Tell the warehouse inbox which orders were handed to the carrier."""
        self.send_many(
            [
                Message(
                    to=self.warehouse_email,
                    subject=f"Order {order_id} handed to the carrier",
                    body="Filed for the end of day manifest.",
                    dedupe_key=f"warehouse-digest:{order_id}",
                )
                for order_id in order_ids
            ]
        )
