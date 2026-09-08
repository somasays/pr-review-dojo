"""Hidden tests for exercise 47: pickup lockout."""

from __future__ import annotations

import inspect
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from sandbox.lockers.lockout import (
    AlertTransientError,
    LockoutPolicy,
    LockoutTracker,
    seconds_remaining,
)


class _FailingNotifier:
    """A notifier stub that always fails, shaped to work whether the caller
    treats it as a callable or as an object with a `.notify` method."""

    def __init__(self) -> None:
        self.calls = 0

    def _fail(self, locker_id: int, locked_until: object) -> None:
        self.calls += 1
        raise AlertTransientError("paging gateway down")

    def __call__(self, locker_id: int, locked_until: object) -> None:
        self._fail(locker_id, locked_until)

    def notify(self, locker_id: int, locked_until: object) -> None:
        self._fail(locker_id, locked_until)


# CC-04: get_lockout_tracker built a new tracker (and started a second
# background thread) for every request thread that raced the None check.
def test_get_lockout_tracker_is_a_true_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    from sandbox.lockers import api

    monkeypatch.setattr(api, "_tracker", None)
    original_init = LockoutTracker.__init__

    def slow_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        original_init(self, *args, **kwargs)
        time.sleep(0.01)

    monkeypatch.setattr(LockoutTracker, "__init__", slow_init)
    barrier = threading.Barrier(8)

    def call() -> LockoutTracker:
        barrier.wait()
        return api.get_lockout_tracker()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: call(), range(8)))

    try:
        assert all(r is results[0] for r in results)
        sweepers = [t for t in threading.enumerate() if t.name == "lockout-sweeper"]
        assert len(sweepers) == 1
    finally:
        results[0].stop(timeout=0.5)


# SV-03: after the bounded retry exhausted, the exercise branch logged and
# returned as if the page had gone out, so ops never learns a lockout was
# never announced.
def test_alert_failure_is_not_silently_swallowed() -> None:
    policy = LockoutPolicy(max_attempts=1, window_seconds=60, lockout_minutes=5, sweep_seconds=3600)
    notifier = _FailingNotifier()
    tracker = LockoutTracker(policy, notifier)
    tracker.record_failure(1)  # triggers the lockout

    raised = False
    try:
        tracker.alert(1)
    except Exception:
        raised = True

    assert notifier.calls == 3  # bounded retry: exactly the attempt cap, not fewer or more
    assert raised, "alert() must not return normally when every retry attempt failed"


# LG-11: the locked-until timestamp was built with an aware datetime.now(UTC)
# while every other timestamp in this API is a naive UTC value.
def test_locked_until_is_a_naive_datetime_like_every_other_timestamp() -> None:
    policy = LockoutPolicy(max_attempts=1, window_seconds=60, lockout_minutes=5, sweep_seconds=3600)
    tracker = LockoutTracker(policy)
    tracker.record_failure(1)
    status = tracker.status(1)
    assert status is not None
    assert status.locked_until.tzinfo is None


# CC-13: the sweep loop polled with time.sleep against a plain flag, so
# stop() could not wake it before the next sleep finished.
def test_stop_does_not_wait_for_the_full_sweep_interval() -> None:
    policy = LockoutPolicy(max_attempts=5, window_seconds=60, lockout_minutes=5, sweep_seconds=30)
    tracker = LockoutTracker(policy)
    tracker.start()
    tracker.stop(timeout=1)
    sweepers = [t for t in threading.enumerate() if t.name == "lockout-sweeper"]
    assert not any(t.is_alive() for t in sweepers)


# DS-04: the notifier was typed and defaulted to a concrete class instead of
# the callable seam this codebase already uses for sweeper.py's Notifier.
def test_notifier_is_a_callable_seam_not_a_concrete_class() -> None:
    annotation = str(inspect.signature(LockoutTracker.__init__).parameters["notifier"].annotation)
    assert "_LogAlertNotifier" not in annotation


# DS-09: seconds_remaining read time.monotonic() internally instead of
# taking `now`, so it could not be pinned in a test or honor an injected
# clock.
def test_seconds_remaining_takes_now_as_a_parameter() -> None:
    params = list(inspect.signature(seconds_remaining).parameters)
    assert "now" in params
    assert seconds_remaining(100.0, 40.0) == 60


# DS-21 (refactor): the minutes/seconds formatting for the 423 response was
# inlined in the exception handler instead of a small, testable function.
def test_retry_after_formatting_is_a_pure_function() -> None:
    from sandbox.lockers import api

    assert hasattr(api, "_format_retry_after"), (
        "pull the minutes/seconds formatting out of the LockedOut handler"
    )
    assert api._format_retry_after(125) == "2m 5s"
    assert api._format_retry_after(120) == "2m"
    assert api._format_retry_after(45) == "45s"
