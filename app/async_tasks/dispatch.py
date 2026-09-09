"""Run one pass of the confirmation dispatcher.

    python -m app.async_tasks.dispatch --limit 100

Enqueues a single dispatch task, drains the worker, and prints what went out.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.async_tasks.handlers import (
    BATCH_LIMIT,
    DISPATCH_KIND,
    metrics,
    register_handlers,
)
from app.async_tasks.worker import QueueWorker, Task
from app.services.config import get_settings
from app.services.notification import BatchNotifier, LoggingAsyncSender

log = logging.getLogger(__name__)


def build_worker(limit: int) -> tuple[QueueWorker, BatchNotifier]:
    settings = get_settings()
    queue: asyncio.Queue[Task] = asyncio.Queue()
    worker = QueueWorker(queue, concurrency=settings.worker_concurrency)
    notifier = BatchNotifier(LoggingAsyncSender())
    register_handlers(worker, notifier)
    queue.put_nowait(Task(DISPATCH_KIND, {"limit": limit}))
    return worker, notifier


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send pending order confirmations")
    parser.add_argument("--limit", type=int, default=BATCH_LIMIT)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    worker, notifier = build_worker(args.limit)
    worker.drain()
    print(f"sent={notifier.stats.sent} skipped={notifier.stats.skipped} metrics={len(metrics)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
