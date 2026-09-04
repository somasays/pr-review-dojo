"""Hidden tests for exercise 08."""

import asyncio
import time
from contextlib import contextmanager

from app.async_tasks import handlers
from app.async_tasks.worker import QueueWorker, Task
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


async def test_concurrent_batches_send_a_dedupe_key_once():
    sender = InMemoryAsyncSender(latency=0.05)
    notifier = BatchNotifier(sender)

    await asyncio.gather(
        notifier.send_batch(_messages([9])),
        notifier.send_batch(_messages([9])),
    )

    assert len(sender.sent) == 1
    assert notifier.stats.sent == 1
    assert notifier.stats.skipped == 1


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


async def test_drain_works_from_a_thread_with_no_event_loop():
    seen: list[int] = []

    def run_worker() -> int:
        queue: asyncio.Queue[Task] = asyncio.Queue()
        worker = QueueWorker(queue, concurrency=2)
        worker.register("noop", lambda payload: seen.append(payload["n"]))
        queue.put_nowait(Task("noop", {"n": 1}))
        worker.drain()
        return worker.stats.processed

    processed = await asyncio.wait_for(asyncio.to_thread(run_worker), timeout=10)

    assert processed == 1
    assert seen == [1]
