"""Hidden tests for exercise 08."""

import asyncio
import inspect
import time
from contextlib import contextmanager

from app.async_tasks import handlers
from app.services.notification import (
    BatchNotifier,
    InMemoryAsyncSender,
    Message,
    confirmation_message,
)


def _messages(order_ids):
    return [confirmation_message(f"c{i}@example.com", i, "10.00 USD") for i in order_ids]


async def test_one_failed_send_does_not_lose_the_rest_of_the_batch():
    sender = InMemoryAsyncSender(fail_keys={"order-confirmed:2"})
    notifier = BatchNotifier(sender)

    results = await notifier.send_batch(_messages([1, 2, 3]))

    assert len(results) == 3
    assert isinstance(results[0], Message)
    assert isinstance(results[1], BaseException)
    assert isinstance(results[2], Message)
    assert sorted(m.dedupe_key for m in sender.sent) == [
        "order-confirmed:1",
        "order-confirmed:3",
    ]
    assert notifier.stats.sent == 2


class _EmptyOrderRepository:
    def __init__(self, session):
        self.session = session

    def list_by_status(self, status, limit=100):
        return []


async def test_dispatch_handler_does_not_block_the_event_loop(monkeypatch):
    ticks: list[float] = []

    async def heartbeat() -> None:
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.01)

    @contextmanager
    def slow_session_scope():
        time.sleep(0.4)
        yield object()

    monkeypatch.setattr(handlers, "session_scope", slow_session_scope)
    monkeypatch.setattr(handlers, "OrderRepository", _EmptyOrderRepository)

    handler = handlers.build_dispatch_handler(BatchNotifier(InMemoryAsyncSender()))
    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    await handler({"limit": 10})
    await asyncio.sleep(0.05)
    beat.cancel()

    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    assert gaps, "heartbeat never ran"
    assert max(gaps) < 0.15


class _FakeResponse:
    status_code = 200


async def test_gateway_health_check_does_not_block_the_event_loop(monkeypatch):
    ticks: list[float] = []

    async def heartbeat() -> None:
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.01)

    def slow_get(url, timeout=None):
        time.sleep(0.3)
        return _FakeResponse()

    monkeypatch.setattr(handlers.httpx, "get", slow_get)
    handlers.metrics.clear()

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    await handlers.check_gateway_health({"url": "http://gateway.example/ping"})
    await asyncio.sleep(0.05)
    beat.cancel()

    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    assert gaps, "heartbeat never ran"
    assert max(gaps) < 0.15
    assert handlers.metrics[-1] == ("gateway_status", 200)


def test_resend_failed_depends_on_the_sender_protocol():
    """DS-04: a fake sender must be injectable, not a hardcoded concrete class."""
    sig = inspect.signature(handlers.resend_failed)
    assert "sender" in sig.parameters
    assert sig.parameters["sender"].annotation == "AsyncSender"
    assert "LoggingAsyncSender" not in inspect.getsource(handlers)


def test_format_resend_report_is_a_pure_function():
    """DS-21: the CSV formatting step must not be tangled with the send."""
    assert not asyncio.iscoroutinefunction(handlers.format_resend_report)
    report = handlers.format_resend_report([confirmation_message("a@example.com", 1, "1.00")])
    assert report == "order,recipient\norder-confirmed:1,a@example.com"


def test_resend_helpers_avoid_a_boolean_mode_switch():
    """Refactor (DS-11): preview and resend should be two functions, not a flag."""
    sig = inspect.signature(handlers.resend_failed)
    assert all(p.annotation != "bool" for p in sig.parameters.values())
    assert handlers.preview_resend([7]) == "order,recipient\norder-confirmed:7,order7@example.com"
