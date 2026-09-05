"""Hidden tests for exercise 27."""

import asyncio
import threading

from app.async_tasks.worker import QueueWorker, RetryAfter, Task
from app.services.config import Settings
from app.services.notification import Message, NotificationService
from app.services.webhooks import WebhookDispatcher, WebhookEndpoint, WebhookEvent

SETTINGS = Settings(
    webhook_attempts=2,
    webhook_max_parallel=4,
    webhook_timeout_ms=500,
    notify_backoff_seconds=0.0,
)
EVENT = WebhookEvent("evt-1", "order.paid", {"order_id": 1})
HOOK = WebhookEndpoint("https://a.example/hook")


class ThreadRecordingSender:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.threads: list[int] = []

    def send(self, message: Message) -> None:
        self.threads.append(threading.get_ident())
        self.sent.append(message)


class SlowTransport:
    def __init__(self, delay: float = 0.02) -> None:
        self.delay = delay
        self.posts: list[str] = []

    async def post(self, url: str, body: dict) -> int:
        self.posts.append(url)
        await asyncio.sleep(self.delay)
        return 200


async def test_send_batch_keeps_the_blocking_sender_off_the_loop_thread():
    sender = ThreadRecordingSender()
    service = NotificationService(sender, SETTINGS)

    await service.send_batch([Message("ops@example.com", "s", "b", "k1")])

    assert len(sender.sent) == 1
    assert threading.get_ident() not in sender.threads


async def test_attempt_timeout_is_read_as_seconds():
    settings = Settings(webhook_attempts=1, webhook_timeout_ms=50, notify_backoff_seconds=0.0)
    dispatcher = WebhookDispatcher(SlowTransport(delay=30), settings)

    result = await asyncio.wait_for(dispatcher.deliver(HOOK, EVENT), timeout=2)

    assert result.ok is False
    assert result.error == "timeout"


async def test_concurrent_delivery_of_one_event_posts_once():
    transport = SlowTransport()
    dispatcher = WebhookDispatcher(transport, SETTINGS)

    await asyncio.gather(dispatcher.deliver(HOOK, EVENT), dispatcher.deliver(HOOK, EVENT))

    assert transport.posts == [HOOK.url]


async def test_retry_after_puts_the_task_back_on_the_queue():
    q: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(q, concurrency=2, max_attempts=3, poll_timeout=0.02)
    calls: list[dict] = []

    async def flaky(payload: dict) -> None:
        calls.append(payload)
        if len(calls) == 1:
            raise RetryAfter(0.01)

    worker.register("flaky", flaky)
    await q.put(Task("flaky", {"n": 1}))

    await asyncio.wait_for(worker.run_until_idle(idle_after=0.05), timeout=5)

    assert len(calls) == 2
    assert worker.stats.processed == 1
    assert worker.stats.retried == 1
    assert q.qsize() == 0


async def test_cancelled_task_is_not_retried():
    q: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(q, concurrency=2, poll_timeout=0.02)
    started = asyncio.Event()

    async def slow(payload: dict) -> None:
        started.set()
        await asyncio.sleep(5)

    worker.register("slow", slow)
    run_task = asyncio.create_task(worker.run())
    await q.put(Task("slow", {}))
    await asyncio.wait_for(started.wait(), timeout=2)

    for task in list(worker._inflight):
        task.cancel()
    await asyncio.sleep(0.05)
    worker.stop()

    await asyncio.wait_for(run_task, timeout=2)
    assert worker.stats.retried == 0
    assert q.qsize() == 0


async def test_idle_callback_failure_does_not_abort_the_drain():
    q: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(q, concurrency=3, poll_timeout=0.02, on_idle=lambda _s: 1 / 0)
    done: list[int] = []

    async def handler(payload: dict) -> None:
        await asyncio.sleep(0.05)
        done.append(1)

    worker.register("h", handler)
    for _ in range(3):
        await q.put(Task("h", {}))

    await asyncio.wait_for(worker.run_until_idle(idle_after=0.05), timeout=5)

    assert len(done) == 3
    assert worker.stats.processed == 3


async def test_drain_owns_its_event_loop():
    q: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(q, concurrency=2, poll_timeout=0.02)
    seen: list[int] = []
    worker.register("noop", lambda p: seen.append(p["n"]))
    q.put_nowait(Task("noop", {"n": 1}))

    await asyncio.to_thread(worker.drain)

    assert seen == [1]


async def test_stop_can_be_requested_from_another_thread():
    q: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(q, poll_timeout=0.02)
    thread = threading.Thread(target=lambda: asyncio.run(worker.run()), daemon=True)
    thread.start()
    for _ in range(200):
        if getattr(worker, "_loop", None) is not None:
            break
        await asyncio.sleep(0.01)

    worker.stop_threadsafe()

    await asyncio.to_thread(thread.join, 2)
    assert not thread.is_alive()
