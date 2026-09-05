"""In-process request rate limiting keyed by API key.

The limiter is a fixed window counter: each key gets `limit` requests per
`window_seconds`, and the window restarts on the first request after it
expires. Counts live in this process only, which is enough for the single
uvicorn worker we run today and keeps the request path free of a round trip
to Redis.

A background sweeper drops keys nobody has touched for two windows so the
map does not grow with every API key we have ever seen.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    retry_after: int


class RateLimiter:
    def __init__(self, policy: RateLimitPolicy) -> None:
        self.policy = policy
        self._lock = threading.Lock()
        self._hits: dict[str, int] = {}
        self._window_start: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def hit(self, key: str) -> Decision:
        """Record one request for `key` and say whether it is allowed."""
        now = time.monotonic()
        with self._lock:
            start = self._window_start.get(key)
            if start is None or now - start >= self.policy.window_seconds:
                start = now
                self._window_start[key] = now
                self._hits[key] = 0
            # A dict item write is atomic, no lock needed.
            used = self._hits.get(key, 0) + 1
            self._hits[key] = used
        elapsed = now - start
        return Decision(
            allowed=used <= self.policy.limit,
            remaining=max(self.policy.limit - used, 0),
            retry_after=max(int(self.policy.window_seconds - elapsed), 0) + 1,
        )

    def snapshot(self) -> dict[str, int]:
        """Current counts per key, for the ops dashboard."""
        with self._lock:
            return dict(self._hits)

    def window_started(self, key: str) -> float | None:
        """When the current window for `key` began, for the ops dashboard."""
        with self._lock:
            return self._window_start.get(key)

    def sweep(self) -> int:
        """Drop keys whose window ended more than one window ago."""
        cutoff = time.monotonic() - 2 * self.policy.window_seconds
        # guard the window map
        with threading.Lock():
            stale = [key for key, start in self._window_start.items() if start < cutoff]
            for key in stale:
                self._window_start.pop(key, None)
                self._hits.pop(key, None)
        return len(stale)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self.policy.window_seconds):
            self.sweep()


def seconds_until_reset(window_start: float, window_seconds: int, now: float) -> int:
    """How long until the window that started at `window_start` rolls over."""
    return max(int(window_seconds - (now - window_start)), 0)
