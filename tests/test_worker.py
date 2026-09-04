import asyncio
import time

import pytest

from app.async_tasks.worker import QueueWorker, Task


async def _drain(worker: QueueWorker) -> None:
    await asyncio.wait_for(worker.run_until_idle(idle_after=0.05), timeout=5)


async def test_dispatches_sync_and_async_handlers():
    q: asyncio.Queue[Task] = asyncio.Queue()
    seen = []
    w = QueueWorker(q, concurrency=2)
    w.register("sync", lambda p: seen.append(("sync", p["n"])))

    async def ahandler(p):
        seen.append(("async", p["n"]))

    w.register("async", ahandler)
    await q.put(Task("sync", {"n": 1}))
    await q.put(Task("async", {"n": 2}))
    await _drain(w)
    assert sorted(seen) == [("async", 2), ("sync", 1)]
    assert w.stats.processed == 2


async def test_retries_then_fails():
    q: asyncio.Queue[Task] = asyncio.Queue()
    attempts = []
    w = QueueWorker(q, max_attempts=3)

    def boom(p):
        attempts.append(1)
        raise RuntimeError("no")

    w.register("boom", boom)
    await q.put(Task("boom", {}))
    await _drain(w)
    assert len(attempts) == 3
    assert w.stats.failed == 1
    assert w.stats.retried == 2
    assert w.stats.errors == ["boom: no"]


async def test_unknown_kind_is_recorded():
    q: asyncio.Queue[Task] = asyncio.Queue()
    w = QueueWorker(q)
    await q.put(Task("mystery", {}))
    await _drain(w)
    assert w.stats.errors == ["no handler for mystery"]


async def test_blocking_handler_does_not_block_loop():
    q: asyncio.Queue[Task] = asyncio.Queue()
    w = QueueWorker(q, concurrency=4)
    w.register("sleep", lambda p: time.sleep(0.2))
    for _ in range(4):
        await q.put(Task("sleep", {}))
    started = time.perf_counter()
    await _drain(w)
    assert time.perf_counter() - started < 0.7
    assert w.stats.processed == 4


@pytest.mark.parametrize("concurrency", [1, 3])
async def test_concurrency_bound(concurrency):
    q: asyncio.Queue[Task] = asyncio.Queue()
    w = QueueWorker(q, concurrency=concurrency)
    active, peak = 0, 0

    async def h(p):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1

    w.register("h", h)
    for _ in range(6):
        await q.put(Task("h", {}))
    await _drain(w)
    assert peak == concurrency
