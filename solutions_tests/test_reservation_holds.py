"""Hidden tests for exercise 24.

Every interleaving here is forced with a barrier, an injected delay, or a
monkeypatched hook, so the outcome does not depend on the scheduler.
"""

import ast
import inspect
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import deps, main
from app.services import reservations
from app.services.reservations import ReservationCache


def _run_together(fn, count):
    barrier = threading.Barrier(count)

    def wrapped(*args):
        barrier.wait(timeout=10)
        return fn(*args)

    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(wrapped) for _ in range(count)]
        return [f.result(timeout=10) for f in futures]


def test_reserve_never_grants_more_than_the_free_stock():
    """Five checkouts asking for two units of a five unit SKU: two win."""
    for _ in range(3):
        cache = ReservationCache()
        original = cache.available

        def slow_available(sku, stock, _original=original):
            value = _original(sku, stock)
            # Every caller finishes its check before any caller writes.
            time.sleep(0.01)
            return value

        cache.available = slow_available  # type: ignore[method-assign]

        results = _run_together(lambda: cache.reserve("GADGET", 2, stock=5), 5)

        granted = [hold for hold in results if hold is not None]
        assert len(granted) == 2
        assert sum(cache._held.values()) <= 5


def test_reservation_cache_is_built_and_loaded_once(monkeypatch):
    loads = []
    real_init = ReservationCache.__init__

    def slow_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        # Widen the window every caller of a cold cache races through.
        time.sleep(0.01)

    def slow_load(self):
        loads.append(self)
        time.sleep(0.01)

    monkeypatch.setattr(ReservationCache, "__init__", slow_init)
    monkeypatch.setattr(ReservationCache, "load", slow_load)
    monkeypatch.setattr(deps, "_reservation_cache", None)

    caches = _run_together(deps.get_reservation_cache, 8)

    assert len(loads) == 1
    assert all(cache is caches[0] for cache in caches)


def test_persist_keeps_the_previous_snapshot_when_writing_fails(tmp_path, monkeypatch):
    path = tmp_path / "reservations.json"
    cache = ReservationCache(path)
    assert cache.reserve("GADGET", 2, stock=5) is not None
    cache.persist()
    original = json.loads(path.read_text())
    assert len(original) == 1

    def half_written(_obj, fh):
        fh.write('{"broken": ')
        raise ValueError("serializer failed")

    monkeypatch.setattr(reservations.json, "dump", half_written)
    with pytest.raises(ValueError):
        cache.persist()

    assert json.loads(path.read_text()) == original


def test_concurrent_persist_never_exposes_a_half_written_file(tmp_path):
    path = tmp_path / "reservations.json"
    caches = []
    for size in range(1, 5):
        cache = ReservationCache(path)
        for n in range(size):
            cache.reserve(f"SKU{n}", 1, stock=10)
        caches.append(cache)
    caches[0].persist()

    stop = threading.Event()
    errors: list[str] = []

    def read_forever():
        while not stop.is_set():
            try:
                json.loads(path.read_text())
            except ValueError as exc:
                errors.append(str(exc))
                return

    reader = threading.Thread(target=read_forever, name="snapshot-reader")
    reader.start()
    try:
        barrier = threading.Barrier(len(caches))

        def write(cache):
            barrier.wait(timeout=10)
            for _ in range(50):
                cache.persist()

        with ThreadPoolExecutor(max_workers=len(caches)) as pool:
            for future in [pool.submit(write, cache) for cache in caches]:
                future.result(timeout=10)
    finally:
        stop.set()
        reader.join(timeout=5)

    assert errors == []
    assert isinstance(json.loads(path.read_text()), dict)


def test_app_shutdown_stops_the_sweep_and_snapshots_the_holds(tmp_path, monkeypatch):
    path = tmp_path / "reservations.json"
    cache = ReservationCache(path)
    monkeypatch.setattr(main, "get_reservation_cache", lambda: cache)

    with TestClient(main.create_app()):
        assert cache.reserve("GADGET", 2, stock=5) is not None
        thread = cache._thread
        assert thread is not None and thread.is_alive()
        assert not thread.daemon

    thread.join(timeout=2)
    assert not thread.is_alive()
    snapshot = json.loads(path.read_text())
    assert [entry[0] for entry in snapshot.values()] == ["GADGET"]


def test_reserve_takes_an_injectable_now():
    """DS-09: the clock is a parameter, the way `expire` already takes one, so a test can pin it."""
    assert "now" in inspect.signature(ReservationCache.reserve).parameters

    cache = ReservationCache()
    fixed = datetime(2030, 1, 1, tzinfo=UTC)
    hold = cache.reserve("GADGET", 1, stock=5, now=fixed)
    assert hold is not None
    assert hold.expires_at == fixed + cache.ttl


def test_format_snapshot_is_a_pure_function():
    """DS-21: the formatting step is importable and needs no session or IO."""
    hold = reservations.Hold("tok", "GADGET", 2, datetime(2030, 1, 1, tzinfo=UTC))
    assert reservations.format_snapshot({"tok": hold}) == {
        "tok": ["GADGET", 2, "2030-01-01T00:00:00+00:00"]
    }


def test_reservations_reuse_the_domain_clock_helper():
    """Refactor (DS-08): no raw datetime.now calls, the shared utcnow() helper instead."""
    tree = ast.parse(Path(reservations.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "now":
            value = node.value
            assert getattr(value, "id", None) != "datetime", (
                "reservations.py should call app.domain.dates.utcnow(), not datetime.now"
            )
    assert any(
        isinstance(n, ast.ImportFrom) and n.module == "app.domain.dates" for n in ast.walk(tree)
    )
