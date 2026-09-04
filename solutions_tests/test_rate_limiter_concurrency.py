"""Hidden tests for exercise 15.

Every interleaving here is forced with a barrier or an injected delay so the
result does not depend on how the interpreter happens to schedule threads.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from app.services.rate_limiter import RateLimiter, RateLimitPolicy

STEP = 0.05


class SlowGetDict(dict):
    """A dict whose ``get`` is slow, so an unguarded read-modify-write loses updates."""

    def get(self, key: Any, default: Any = None) -> Any:  # noqa: ANN401
        time.sleep(STEP)
        return super().get(key, default)


class SlowItemsDict(dict):
    """A dict whose ``items`` iterates slowly over the real view."""

    def items(self) -> Any:  # noqa: ANN401
        for pair in super().items():
            time.sleep(2 * STEP)
            yield pair


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


def test_sweep_excludes_a_concurrent_hit(limiter: RateLimiter) -> None:
    """CC-07: the sweep has to hold the limiter's own lock, not a fresh one."""
    old = time.monotonic() - 600
    for key in ("a", "b", "c"):
        limiter.hit(key)
        limiter._window_start[key] = old
    limiter._window_start = SlowItemsDict(limiter._window_start)

    errors: list[BaseException] = []
    swept: list[int] = []

    def sweep() -> None:
        try:
            swept.append(limiter.sweep())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=sweep)
    thread.start()
    time.sleep(STEP / 2)
    limiter.hit("late")
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    assert swept == [3]
    assert limiter.snapshot()["late"] == 1


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


def test_sweeper_thread_has_a_readable_name() -> None:
    """CC-16: an unnamed thread shows up as Thread-7 in every incident dump."""
    made = RateLimiter(RateLimitPolicy(limit=10, window_seconds=30))
    made.start()
    try:
        name = made._thread.name
        assert not re.match(r"^Thread-\d", name)
        assert name.strip()
    finally:
        made.stop()


def test_no_atomicity_claim_in_the_touched_modules() -> None:
    """CC-17: the comment teaches the next reader a rule that is not true."""
    for path in ("app/services/rate_limiter.py", "app/api/deps.py"):
        assert "atomic" not in Path(path).read_text()
