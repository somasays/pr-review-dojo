"""Customer notifications.

The sender is injected so tests and the async worker can swap it out. Sends
are retried because the email gateway is flaky, and every message carries a
dedupe key so a retry after a partial success does not double-send.

`BatchNotifier` is the async counterpart used by the queue worker when a
whole batch of confirmations has to go out at once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.services.config import Settings, get_settings
from app.services.retry import RetryPolicy, retry

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    body: str
    dedupe_key: str


class Sender(Protocol):
    def send(self, message: Message) -> None: ...


class AsyncSender(Protocol):
    async def send(self, message: Message) -> None: ...


def confirmation_message(email: str, order_id: int, total: str) -> Message:
    """The order confirmation every path sends, sync or batched."""
    return Message(
        to=email,
        subject=f"Order {order_id} confirmed",
        body=f"Thanks. Your total is {total}.",
        dedupe_key=f"order-confirmed:{order_id}",
    )


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


@dataclass
class InMemoryAsyncSender:
    """Async sender used by tests and local development."""

    sent: list[Message] = field(default_factory=list)
    fail_keys: set[str] = field(default_factory=set)
    latency: float = 0.0

    async def send(self, message: Message) -> None:
        if self.latency:
            await asyncio.sleep(self.latency)
        if message.dedupe_key in self.fail_keys:
            raise ConnectionError("gateway unavailable")
        self.sent.append(message)


@dataclass
class LoggingAsyncSender:
    """Stand-in for the real gateway client used by the dispatcher script."""

    async def send(self, message: Message) -> None:
        log.info("would send %s to %s (key=%s)", message.subject, message.to, message.dedupe_key)


@dataclass
class BatchStats:
    sent: int = 0
    skipped: int = 0


class NotificationService:
    def __init__(self, sender: Sender, settings: Settings | None = None) -> None:
        self.sender = sender
        settings = settings or get_settings()
        self.policy = RetryPolicy(
            attempts=settings.notify_retries, backoff_seconds=settings.notify_backoff_seconds
        )

    def _deliver(self, message: Message) -> None:
        log.info("sending %s to %s (key=%s)", message.subject, message.to, message.dedupe_key)
        retry(lambda: self.sender.send(message), self.policy, sleep=lambda _s: None)

    def order_confirmed(self, email: str, order_id: int, total: str) -> None:
        self._deliver(confirmation_message(email, order_id, total))

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


class BatchNotifier:
    """Sends a batch of messages over an async gateway.

    The queue worker hands us every confirmation it found in one call, so the
    sends fan out instead of running one after another. Keys already sent by
    this process are skipped, which keeps a re-run of the same batch cheap.
    """

    def __init__(self, sender: AsyncSender) -> None:
        self.sender = sender
        self.stats = BatchStats()
        self._seen: set[str] = set()

    async def _send_one(self, message: Message) -> Message:
        if message.dedupe_key in self._seen:
            self.stats.skipped += 1
            return message
        # Claim the key before the await, otherwise a concurrent batch with the
        # same key passes the check above and both sends go out.
        self._seen.add(message.dedupe_key)
        log.info("sending %s to %s (key=%s)", message.subject, message.to, message.dedupe_key)
        try:
            await self.sender.send(message)
        except BaseException:
            self._seen.discard(message.dedupe_key)
            raise
        self.stats.sent += 1
        return message

    async def send_batch(self, messages: Sequence[Message]) -> list[Message | BaseException]:
        """Send every message and return one result per message, in order."""
        if not messages:
            return []
        futs = [asyncio.ensure_future(self._send_one(m)) for m in messages]
        results: list[Message | BaseException] = list(
            await asyncio.gather(*futs, return_exceptions=True)
        )
        for message, result in zip(messages, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("send failed for %s: %r", message.dedupe_key, result)
        log.info(
            "batch of %d done: %d sent, %d skipped",
            len(messages),
            self.stats.sent,
            self.stats.skipped,
        )
        return results
