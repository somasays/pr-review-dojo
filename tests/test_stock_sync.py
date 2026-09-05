import asyncio
from contextlib import contextmanager

import pytest

from app.async_tasks.stock_sync import StockSyncService, register_handlers
from app.async_tasks.worker import QueueWorker, Task
from app.services.supplier import InMemorySupplierClient, SupplierStock


@pytest.fixture
def patched_scope(monkeypatch, db, seeded):
    @contextmanager
    def _scope():
        yield db

    monkeypatch.setattr("app.async_tasks.stock_sync.session_scope", _scope)
    return db


async def test_in_memory_client_reports_levels_and_gaps():
    client = InMemorySupplierClient(levels={"WIDGET": 12})
    assert await client.fetch("WIDGET") == SupplierStock(sku="WIDGET", quantity=12)
    assert await client.fetch("NOPE") is None
    assert client.requested == ["WIDGET", "NOPE"]


async def test_sync_skus_writes_new_quantities(patched_scope, seeded):
    products = seeded["products"]
    client = InMemorySupplierClient(levels={"WIDGET": 42, "GADGET": 5})
    service = StockSyncService(client)

    stats = await service.sync_skus(["WIDGET", "GADGET"])

    assert stats.fetched == 2
    assert stats.updated == 1
    assert products["WIDGET"].stock == 42
    assert products["GADGET"].stock == 5


async def test_sync_skus_counts_unknown_skus(patched_scope):
    client = InMemorySupplierClient(levels={"WIDGET": 3})
    service = StockSyncService(client)

    stats = await service.sync_skus(["WIDGET", "NOT-CARRIED"])

    assert stats.missing == 1
    assert stats.errors == []


async def test_worker_dispatches_a_sync_stock_task(patched_scope, seeded):
    queue: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(queue, concurrency=2)
    client = InMemorySupplierClient(levels={"GIZMO": 9})
    service = StockSyncService(client)
    register_handlers(worker, service)

    await queue.put(Task("sync_stock", {"skus": ["GIZMO"]}))
    await asyncio.wait_for(worker.run_until_idle(idle_after=0.05), timeout=5)

    assert worker.stats.processed == 1
    assert seeded["products"]["GIZMO"].stock == 9
