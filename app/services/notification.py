"""Customer notifications.

The sender is injected so tests and the async worker can swap it out. Sends
are retried because the email gateway is flaky, and every message carries a
dedupe key so a retry after a partial success does not double-send.

`NotificationFlusher` batches messages instead of calling the gateway once per
order. Request threads enqueue, a background thread sends whatever is pending
every `NOTIFY_FLUSH_SECONDS` and as soon as a full batch has piled up.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.db.repositories import CustomerRepository
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


def format_flusher_digest(
    pending: int, sent_total: int, error_count: int, last_error: str | None
) -> str:
    """Pure formatting for the ops report, callable with plain values."""
    parts = [f"pending={pending}", f"sent_total={sent_total}"]
    if error_count:
        parts.append(f"errors={error_count} last={last_error}")
    else:
        parts.append("errors=0")
    return ", ".join(parts)


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


class NotificationFlusher:
    """Collects messages and hands the gateway one batch at a time.

    One instance per process. `enqueue` is called from request threads, the
    thread started by `start` drains the queue on a timer, and `stop` sends
    what is left so a deploy does not drop mail.
    """

    def __init__(self, sender: Sender, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        self.sender = sender
        self.policy = RetryPolicy(
            attempts=settings.notify_retries, backoff_seconds=settings.notify_backoff_seconds
        )
        self.batch_size = settings.notify_batch_size
        self.flush_seconds = settings.notify_flush_seconds
        self.sessions: sessionmaker[Session] | None = None
        self.errors: list[str] = []
        self.sent_total = 0
        self._pending: list[Message] = []
        self._batch: list[Message] = []
        self._seen: dict[str, float] = {}
        # One lock for the queue, the batch and the dedupe map. Two locks bought
        # nothing here and could be taken in either order.
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._wake.clear()
        self._thread = threading.Thread(target=self._run, name="notification-flusher")
        self._thread.start()

    def stop(self) -> None:
        """Stop the loop and send whatever is still queued."""
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join()
        self._tick()

    def enqueue(self, message: Message) -> None:
        """Queue one message. Called from request threads."""
        with self._lock:
            self._pending.append(message)
            depth = len(self._pending) + len(self._batch)
        # A partial batch goes out on the next tick of the flusher thread.
        if depth >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """Move everything pending into the batch and send one batch."""
        with self._lock:
            self._batch.extend(self._pending)
            self._pending.clear()
            batch = self._batch[: self.batch_size]
            self._batch = self._batch[self.batch_size :]
        if batch:
            self._flush_batch(batch)

    def compact(self, now: float | None = None) -> None:
        """Forget dedupe keys that are older than the replay window."""
        now = time.monotonic() if now is None else now
        cutoff = now - self.flush_seconds * 10
        with self._lock:
            self._seen = {key: at for key, at in self._seen.items() if at > cutoff}

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def digest(self) -> str:
        """One-line summary of flusher activity for the ops report."""
        with self._lock:
            error_count = len(self.errors)
            last_error = self.errors[-1] if self.errors else None
        return format_flusher_digest(self.pending_count(), self.sent_total, error_count, last_error)

    def _run(self) -> None:
        while not self._wake.wait(self.flush_seconds):
            self.compact()
            self._tick()

    def _tick(self) -> None:
        try:
            self.flush()
        except Exception:
            log.exception("flush failed, %d messages still queued", self.pending_count())

    def _flush_batch(self, batch: list[Message]) -> None:
        active = self._active_recipients(batch)
        now = time.monotonic()
        with self._lock:
            # One send per dedupe key, not per recipient: a customer can have
            # two orders in the same batch.
            fresh = [m for m in batch if m.to in active and m.dedupe_key not in self._seen]
            for message in fresh:
                self._seen[message.dedupe_key] = now
        with ThreadPoolExecutor(max_workers=8, thread_name_prefix="notify") as pool:
            submitted = [(m, pool.submit(self._deliver, m)) for m in fresh]
        failed = []
        for message, future in submitted:
            error = future.exception()
            if error is not None:
                log.warning("could not send %s: %s", message.dedupe_key, error)
                failed.append(message.dedupe_key)
        with self._lock:
            self.errors.extend(failed)
            for key in failed:
                self._seen.pop(key, None)
            self.sent_total += len(fresh) - len(failed)

    def _active_recipients(self, batch: list[Message]) -> set[str]:
        """Skip mail for customers deleted while the batch was waiting."""
        if self.sessions is None:
            return {m.to for m in batch}
        with self.sessions() as session:
            repo = CustomerRepository(session)
            return {m.to for m in batch if repo.by_email(m.to) is not None}

    def _deliver(self, message: Message) -> None:
        log.info("sending %s to %s (key=%s)", message.subject, message.to, message.dedupe_key)
        retry(lambda: self.sender.send(message), self.policy)


class NotificationService:
    def __init__(
        self,
        sender: Sender,
        settings: Settings | None = None,
        flusher: NotificationFlusher | None = None,
    ) -> None:
        self.sender = sender
        settings = settings or get_settings()
        self.policy = RetryPolicy(
            attempts=settings.notify_retries, backoff_seconds=settings.notify_backoff_seconds
        )
        self.flusher = flusher

    def flush_now(self) -> None:
        """Send the queued batch now instead of waiting for the timer."""
        if self.flusher is not None:
            self.flusher.flush()

    def _deliver(self, message: Message) -> None:
        if self.flusher is not None:
            self.flusher.enqueue(message)
            return
        log.info("sending %s to %s (key=%s)", message.subject, message.to, message.dedupe_key)
        retry(lambda: self.sender.send(message), self.policy, sleep=lambda _s: None)

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
