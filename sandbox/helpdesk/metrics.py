"""In-process queue counters, guarded by one lock taken by every reader
and writer. A module-level instance is not created here; the API creates
one per app in `create_app()`."""

from __future__ import annotations

import threading


class QueueMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts = {"created": 0, "claimed": 0, "resolved": 0}

    def record(self, name: str) -> None:
        with self._lock:
            self._counts[name] += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)
