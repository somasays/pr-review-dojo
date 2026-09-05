"""Hidden tests for exercise 31.

Every interleaving is forced with a barrier, a wrapped lock, or a long timer
period, so nothing here depends on the scheduler. Threads started by a test
are daemons and are joined with a timeout, so a deadlock fails the test
instead of hanging the run.
"""

import ast
import dataclasses
import inspect
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.api import deps
from app.services.config import Settings
from app.services.notification import (
    InMemorySender,
    Message,
    NotificationFlusher,
    format_flusher_digest,
)

SLOW_TICK = 30.0  # long enough that no background timer fires during a test


def make_flusher(sender=None, **overrides) -> NotificationFlusher:
    fields = {"notify_retries": 1, "notify_flush_seconds": SLOW_TICK, **overrides}
    settings = Settings(**fields)
    return NotificationFlusher(sender or InMemorySender(), settings)


def msg(to: str, key: str) -> Message:
    return Message(to=to, subject=f"Order {key}", body="hello", dedupe_key=key)


def eventually(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture(autouse=True)
def _cancel_stray_timers():
    yield
    for thread in threading.enumerate():
        if isinstance(thread, threading.Timer):
            thread.cancel()


class SlowLock:
    """A lock that pauses after it is taken, to widen every ordering window."""

    def __init__(self, inner, delay: float = 0.005) -> None:
        self.inner = inner
        self.delay = delay

    def acquire(self, *args, **kwargs):
        got = self.inner.acquire(*args, **kwargs)
        if got:
            time.sleep(self.delay)
        return got

    def release(self) -> None:
        self.inner.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


def lock_attributes(obj) -> list[str]:
    return [name for name, value in vars(obj).items() if "lock" in type(value).__name__.lower()]


def test_enqueue_and_flush_never_deadlock():
    """A request thread queueing while the flusher sends must not wedge."""
    flusher = make_flusher()
    for name in lock_attributes(flusher):
        setattr(flusher, name, SlowLock(getattr(flusher, name)))

    barrier = threading.Barrier(2)
    done: list[str] = []

    def queue_messages():
        barrier.wait(timeout=5)
        for i in range(20):
            flusher.enqueue(msg("ada@example.com", f"queued-{i}"))
        done.append("enqueue")

    def send_batches():
        barrier.wait(timeout=5)
        for _ in range(20):
            flusher.flush()
        done.append("flush")

    threads = [
        threading.Thread(target=queue_messages, name="requester", daemon=True),
        threading.Thread(target=send_batches, name="flusher", daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert [t.name for t in threads if t.is_alive()] == []
    assert sorted(done) == ["enqueue", "flush"]


def test_flusher_does_not_keep_the_request_session(db, seeded):
    """The process wide flusher must not hold a Session owned by one request."""
    deps.get_order_service(db, Settings())
    db.close()

    flusher = deps.get_flusher()
    held = [name for name, value in vars(flusher).items() if isinstance(value, Session)]
    assert held == []


def test_flusher_resolves_recipients_on_its_own_session(db, seeded, session_factory):
    flusher = make_flusher()
    flusher.sessions = session_factory
    flusher.session = db
    flusher.enqueue(msg("ada@example.com", "order-confirmed:1"))
    flusher.enqueue(msg("deleted@example.com", "order-confirmed:2"))

    db.close()
    flusher.flush()

    assert eventually(lambda: len(flusher.sender.sent) == 1)
    assert [m.dedupe_key for m in flusher.sender.sent] == ["order-confirmed:1"]


def test_flushing_does_not_leak_worker_threads():
    flusher = make_flusher()
    baseline = threading.active_count()

    for round_no in range(5):
        for i in range(20):
            flusher.enqueue(msg(f"c{i}@example.com", f"r{round_no}-{i}"))
        flusher.flush()

    assert eventually(lambda: len(flusher.sender.sent) == 100, timeout=2)
    assert eventually(lambda: threading.active_count() <= baseline + 2, timeout=2), (
        f"{threading.active_count() - baseline} threads above the baseline"
    )


def test_a_failed_send_is_recorded():
    class FlakySender:
        def __init__(self, bad_key: str) -> None:
            self.bad_key = bad_key
            self.sent: list[Message] = []

        def send(self, message: Message) -> None:
            if message.dedupe_key == self.bad_key:
                raise ConnectionError("gateway unavailable")
            self.sent.append(message)

    sender = FlakySender("order-shipped:2")
    flusher = make_flusher(sender)
    for order_id in range(1, 5):
        flusher.enqueue(msg(f"c{order_id}@example.com", f"order-shipped:{order_id}"))
    flusher.flush()

    assert flusher.errors == ["order-shipped:2"]
    assert sorted(m.dedupe_key for m in sender.sent) == [
        "order-shipped:1",
        "order-shipped:3",
        "order-shipped:4",
    ]


def test_two_messages_for_one_customer_both_go_out():
    """A customer who pays one order while cancelling another gets both mails."""
    flusher = make_flusher()
    flusher.enqueue(msg("ada@example.com", "order-confirmed:1"))
    flusher.enqueue(msg("ada@example.com", "order-cancelled:2"))

    flusher.flush()

    assert eventually(lambda: len(flusher.sender.sent) == 2)
    assert sorted(m.dedupe_key for m in flusher.sender.sent) == [
        "order-cancelled:2",
        "order-confirmed:1",
    ]


def test_compact_takes_the_flushers_own_lock():
    flusher = make_flusher()
    flusher.enqueue(msg("ada@example.com", "order-confirmed:1"))
    locks = [getattr(flusher, name) for name in lock_attributes(flusher)]
    assert locks, "the flusher owns no lock at all"

    for lock in locks:
        lock.acquire()
    worker = threading.Thread(target=flusher.compact, name="compactor", daemon=True)
    worker.start()
    worker.join(timeout=0.3)
    blocked = worker.is_alive()
    for lock in locks:
        lock.release()

    worker.join(timeout=5)
    assert not worker.is_alive()
    assert blocked, "compact rebuilt the dedupe map while the flusher's locks were held"


def test_reports_router_does_not_build_queries_directly():
    """DS-05: the router should ask a repository, not import sqlalchemy itself."""
    tree = ast.parse(Path("app/api/routers/reports.py").read_text())
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            module_names.add(node.module)
    assert not any(name == "sqlalchemy" or name.startswith("sqlalchemy.") for name in module_names)

    attr_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "execute" not in attr_names
    assert "scalars" not in attr_names


def test_flusher_digest_uses_a_pure_format_function():
    """DS-21: the formatting step must be importable and take plain values."""
    assert format_flusher_digest(2, 5, 0, None) == "pending=2, sent_total=5, errors=0"
    assert format_flusher_digest(0, 0, 1, "order-shipped:9") == (
        "pending=0, sent_total=0, errors=1 last=order-shipped:9"
    )


def test_settings_has_no_speculative_feature_flag():
    """DS-18: no enable_/use_ toggle for a feature with one rollout path."""
    names = [f.name for f in dataclasses.fields(Settings)]
    assert not any(name.startswith("enable_") or name.startswith("use_") for name in names)


def test_compact_takes_now_as_a_parameter():
    """DS-09 (refactor): the expiry cutoff is injectable, not read from the clock."""
    flusher = make_flusher()
    assert "now" in inspect.signature(flusher.compact).parameters

    window = flusher.flush_seconds * 10
    flusher._seen = {"a": 1000.0, "b": 2000.0}

    flusher.compact(now=1000.0 + window - 1)
    assert flusher._seen == {"a": 1000.0, "b": 2000.0}

    flusher.compact(now=2000.0 + window + 1)
    assert flusher._seen == {}
