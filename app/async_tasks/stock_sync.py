"""Stock sync: pull supplier inventory and refresh the product catalog.

The supplier gateway is remote and slow, so the SKU fetches run concurrently
on the event loop and the database write happens once, in a thread, after
every level for the batch has arrived.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.async_tasks.worker import QueueWorker, Task
from app.db.repositories import ProductRepository
from app.db.session import session_scope
from app.services.config import get_settings
from app.services.supplier import HttpSupplierClient, SupplierClient, SupplierStock

log = logging.getLogger(__name__)

DEFAULT_CHUNK = 50


@dataclass
class SyncStats:
    fetched: int = 0
    updated: int = 0
    missing: int = 0
    errors: list[str] = field(default_factory=list)


async def iter_tracked_skus(chunk: int = DEFAULT_CHUNK) -> AsyncIterator[list[str]]:
    """Yield the catalog SKUs in chunks, oldest sync run first."""
    with session_scope() as db:
        skus = await asyncio.to_thread(ProductRepository(db).tracked_skus)
        for start in range(0, len(skus), chunk):
            yield list(skus[start : start + chunk])


class StockSyncService:
    """Fetches supplier levels for a set of SKUs and writes the differences."""

    def __init__(self, client: SupplierClient) -> None:
        self.client = client
        self.stats = SyncStats()

    async def _fetch_one(self, sku: str) -> SupplierStock | None:
        level = await self.client.fetch(sku)
        if level is None:
            self.stats.missing += 1
            return None
        self.stats.fetched += 1
        return level

    async def sync_skus(self, skus: Sequence[str]) -> SyncStats:
        """Refresh one batch of SKUs and return the running totals."""
        pending = [asyncio.ensure_future(self._fetch_one(sku)) for sku in skus]
        results = await asyncio.gather(*pending, return_exceptions=True)

        levels: list[SupplierStock] = []
        for sku, result in zip(skus, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("supplier fetch for %s failed: %s", sku, result)
                self.stats.errors.append(f"{sku}: {result}")
            elif result is not None:
                levels.append(result)

        if levels:
            updated, missing = await asyncio.to_thread(self._apply, levels)
            self.stats.updated += updated
            self.stats.missing += missing
        return self.stats

    def _apply(self, levels: list[SupplierStock]) -> tuple[int, int]:
        """Write the new quantities. Runs in a thread because the session is sync."""
        with session_scope() as db:
            products = ProductRepository(db).by_skus([level.sku for level in levels])
            updated = 0
            missing = 0
            for level in levels:
                product = products.get(level.sku)
                if product is None:
                    missing += 1
                    continue
                if product.stock != level.quantity:
                    product.stock = level.quantity
                    updated += 1
            return updated, missing

    async def sync_catalog(self, chunk: int = DEFAULT_CHUNK, max_chunks: int = 20) -> SyncStats:
        """Walk the catalog a chunk at a time, stopping after max_chunks."""
        done = 0
        async for skus in iter_tracked_skus(chunk):
            await self.sync_skus(skus)
            done += 1
            if done >= max_chunks:
                break
        return self.stats

    def sync_now(self, skus: Sequence[str]) -> SyncStats:
        """Entry point for the admin CLI, which is synchronous."""
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(self.sync_skus(skus))


def register_handlers(worker: QueueWorker, service: StockSyncService) -> None:
    """Wire the stock sync task kinds onto an existing worker."""

    async def sync_stock(payload: dict[str, Any]) -> None:
        await service.sync_skus(payload["skus"])

    async def reset_stats(payload: dict[str, Any]) -> None:
        service.stats = SyncStats()

    worker.register("sync_stock", sync_stock)
    worker.register("reset_stats", reset_stats)


async def _main() -> None:
    settings = get_settings()
    queue: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(queue, concurrency=settings.worker_concurrency)
    service = StockSyncService(HttpSupplierClient(settings.supplier_base_url))
    register_handlers(worker, service)
    await service.sync_catalog()
    await worker.run_until_idle()
    log.info("stock sync finished: %s", service.stats)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
