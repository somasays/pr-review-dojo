"""Hidden tests for exercise 15.

Every interleaving here is forced with a barrier or an injected delay so the
result does not depend on how the interpreter happens to schedule threads.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from app.services.rate_limiter import RateLimiter, RateLimitPolicy

STEP = 0.05


class SlowGetDict(dict):
    """A dict whose ``get`` is slow, so an unguarded read-modify-write loses updates."""

    def get(self, key: Any, default: Any = None) -> Any:  # noqa: ANN401
        value = super().get(key, default)
        time.sleep(STEP)
        return value


@pytest.fixture
def limiter() -> Any:  # noqa: ANN401
    made = RateLimiter(RateLimitPolicy(limit=1000, window_seconds=60))
    yield made
    made.stop()


def test_concurrent_hits_on_one_key_do_not_lose_counts(limiter: RateLimiter) -> None:
    """CC-06: the counter read, the add, and the write have to be one critical section."""
    limiter.hit("key")
    limiter._hits = SlowGetDict(limiter._hits)

    barrier = threading.Barrier(4)

    def call() -> None:
        barrier.wait()
        limiter.hit("key")

    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in [pool.submit(call) for _ in range(4)]:
            future.result()

    assert limiter.snapshot()["key"] == 5


def test_get_rate_limiter_builds_exactly_one_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """CC-04: the lazy singleton is built during a request, so it needs a lock."""
    from app.api import deps

    built: list[RateLimiter] = []
    original = RateLimiter.__init__

    def slow_init(self: RateLimiter, policy: RateLimitPolicy) -> None:
        built.append(self)
        time.sleep(2 * STEP)
        original(self, policy)

    monkeypatch.setattr(RateLimiter, "__init__", slow_init)
    monkeypatch.setattr(deps, "_rate_limiter", None, raising=False)
    barrier = threading.Barrier(8)

    def call() -> RateLimiter:
        barrier.wait()
        return deps.get_rate_limiter()

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = [f.result() for f in [pool.submit(call) for _ in range(8)]]
        assert len(built) == 1
        assert all(r is results[0] for r in results)
    finally:
        for made in built:
            made.stop()
        monkeypatch.setattr(deps, "_rate_limiter", None, raising=False)


def test_stop_wakes_the_sweeper_immediately() -> None:
    """CC-13: a long time.sleep means shutdown waits for a whole window."""
    made = RateLimiter(RateLimitPolicy(limit=10, window_seconds=30))
    made.start()
    made.stop()
    made._thread.join(timeout=2)
    assert not made._thread.is_alive()
