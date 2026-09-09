from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from sandbox.helpdesk.metrics import QueueMetrics


def test_metrics_counters_under_threadpool_barrier() -> None:
    metrics = QueueMetrics()
    workers = 8
    calls_per_worker = 50
    barrier = threading.Barrier(workers)

    def _hammer() -> None:
        barrier.wait()
        for _ in range(calls_per_worker):
            metrics.record("created")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda _: _hammer(), range(workers)))

    assert metrics.snapshot()["created"] == workers * calls_per_worker
