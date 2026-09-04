"""Hidden tests for exercise 16."""

import ast
import asyncio
import inspect
import time
import typing

import pytest

from app.async_tasks import stock_sync
from app.async_tasks.stock_sync import StockSyncService
from app.async_tasks.worker import QueueWorker, Task
from app.services.notification import Sender
from app.services.supplier import HttpSupplierClient


class _CountingClient:
    """Records how many fetches are in flight at the same time."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def fetch(self, sku):
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return None


class _HangingHttpClient:
    async def get(self, url):
        await asyncio.sleep(5)
        raise AssertionError("the request should have been cut off")


async def test_supplier_fan_out_is_bounded():
    client = _CountingClient()
    service = StockSyncService(client)

    await service.sync_skus([f"SKU-{i}" for i in range(50)])

    assert client.peak <= 8, f"{client.peak} concurrent supplier calls"


async def test_supplier_fetch_has_a_deadline():
    client = HttpSupplierClient(
        "http://supplier.test", client=_HangingHttpClient(), timeout_seconds=0.2
    )
    started = time.perf_counter()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(client.fetch("WIDGET"), timeout=3)

    assert time.perf_counter() - started < 1.0


async def test_shutdown_waits_for_inflight_tasks():
    queue: asyncio.Queue[Task] = asyncio.Queue()
    done = []
    worker = QueueWorker(queue, concurrency=2, poll_timeout=0.01)

    async def slow(payload):
        await asyncio.sleep(0.1)
        done.append(payload["n"])

    worker.register("slow", slow)
    await queue.put(Task("slow", {"n": 1}))
    await queue.put(Task("slow", {"n": 2}))

    run_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.05)
    worker.stop()
    await asyncio.wait_for(run_task, timeout=3)

    assert sorted(done) == [1, 2]
    assert worker.stats.processed == 2


async def test_task_timeout_does_not_retry_a_running_handler():
    queue: asyncio.Queue[Task] = asyncio.Queue()
    calls = []
    worker = QueueWorker(queue, max_attempts=3, poll_timeout=0.01, task_timeout=0.1)

    def slow(payload):
        time.sleep(0.3)
        calls.append(1)

    worker.register("slow", slow)
    await queue.put(Task("slow", {}))

    await asyncio.wait_for(worker.run_until_idle(idle_after=0.05), timeout=5)
    await asyncio.sleep(0.9)

    assert len(calls) == 1
    assert worker.stats.retried == 0
    assert worker.stats.failed == 1


def test_low_stock_alert_depends_on_the_sender_protocol():
    hints = typing.get_type_hints(stock_sync.notify_low_stock)
    assert hints["sender"] is Sender
    assert "InMemorySender" not in vars(stock_sync)


def test_low_stock_alert_reuses_the_retry_helper():
    tree = ast.parse(inspect.getsource(stock_sync.notify_low_stock))
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "retry" in called_names
    hand_rolled = any(
        isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and getattr(node.iter.func, "id", None) == "range"
        for node in ast.walk(tree)
    )
    assert not hand_rolled


def test_low_stock_message_formatting_is_pure():
    message = stock_sync.format_low_stock_message("WIDGET", 1)

    assert message.subject == "Low stock: WIDGET"
    assert "1" in message.body
