import asyncio

from app.async_tasks.handlers import METRIC_KIND, metrics, record_metric
from app.async_tasks.worker import QueueWorker, Task
from app.services.notification import BatchNotifier, InMemoryAsyncSender, confirmation_message


def _messages(order_ids):
    return [confirmation_message(f"c{i}@example.com", i, "10.00 USD") for i in order_ids]


async def test_batch_sends_every_message():
    sender = InMemoryAsyncSender()
    notifier = BatchNotifier(sender)
    results = await notifier.send_batch(_messages([1, 2, 3]))
    assert len(results) == 3
    assert [m.dedupe_key for m in sender.sent] == [
        "order-confirmed:1",
        "order-confirmed:2",
        "order-confirmed:3",
    ]
    assert notifier.stats.sent == 3


async def test_empty_batch_is_a_no_op():
    notifier = BatchNotifier(InMemoryAsyncSender())
    assert await notifier.send_batch([]) == []
    assert notifier.stats.sent == 0


async def test_second_batch_skips_keys_already_sent():
    sender = InMemoryAsyncSender()
    notifier = BatchNotifier(sender)
    await notifier.send_batch(_messages([4]))
    await notifier.send_batch(_messages([4]))
    assert len(sender.sent) == 1
    assert notifier.stats.skipped == 1


async def test_one_bad_send_does_not_lose_the_rest_of_the_batch():
    sender = InMemoryAsyncSender(fail_keys={"order-confirmed:2"})
    notifier = BatchNotifier(sender)
    results = await notifier.send_batch(_messages([1, 2, 3]))
    assert len(results) == 3
    assert isinstance(results[1], BaseException)
    assert sorted(m.dedupe_key for m in sender.sent) == [
        "order-confirmed:1",
        "order-confirmed:3",
    ]
    assert notifier.stats.sent == 2


async def test_metric_handler_runs_through_the_worker():
    metrics.clear()
    q: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(q, concurrency=2)
    worker.register(METRIC_KIND, record_metric)
    await q.put(Task(METRIC_KIND, {"name": "confirmations", "value": 3}))
    await asyncio.wait_for(worker.run_until_idle(idle_after=0.05), timeout=5)
    assert metrics == [("confirmations", 3)]
    assert worker.stats.processed == 1
