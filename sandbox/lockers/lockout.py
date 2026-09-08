"""Brute-force protection for pickup: an in-process, per-locker tracker of
failed pickup codes.

After too many wrong codes for one locker in a rolling window the locker
locks out; a background thread expires lockouts and prunes idle counters.
Policy values come from LOCKERS_LOCKOUT_* environment variables.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from os import environ

log = logging.getLogger(__name__)

_ALERT_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class LockoutPolicy:
    max_attempts: int
    window_seconds: int
    lockout_minutes: int
    sweep_seconds: float


def _int_env(name: str, default: int) -> int:
    raw = environ.get(name)
    return int(raw) if raw else default


@lru_cache(maxsize=1)
def load_lockout_policy() -> LockoutPolicy:
    """Read the lockout policy from the environment. Cached for the process."""
    return LockoutPolicy(
        max_attempts=_int_env("LOCKERS_LOCKOUT_MAX_ATTEMPTS", 5),
        window_seconds=_int_env("LOCKERS_LOCKOUT_WINDOW_SECONDS", 300),
        lockout_minutes=_int_env("LOCKERS_LOCKOUT_MINUTES", 15),
        sweep_seconds=float(_int_env("LOCKERS_LOCKOUT_SWEEP_SECONDS", 30)),
    )


@dataclass(frozen=True, slots=True)
class LockoutStatus:
    retry_after_seconds: int
    locked_until: datetime


class AlertTransientError(Exception):
    """Raised by a notifier for a failure the caller should retry."""


def seconds_remaining(deadline: float) -> int:
    """Whole seconds left before a monotonic deadline, floored at zero."""
    return max(0, int(deadline - time.monotonic()))


class _LogAlertNotifier:
    """Default alert notifier: writes a warning to the module logger."""

    def notify(self, locker_id: int, locked_until: datetime) -> None:
        log.warning(
            "locker %s locked out until %s after repeated wrong codes",
            locker_id,
            locked_until.isoformat(),
        )


class LockoutTracker:
    """Per-locker failed pickup-code counter shared across request threads."""

    def __init__(
        self,
        policy: LockoutPolicy,
        notifier: _LogAlertNotifier | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._notifier = notifier or _LogAlertNotifier()
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[int, list[float]] = {}
        self._locked_until: dict[int, float] = {}
        self._last_seen: dict[int, float] = {}
        self._thread: threading.Thread | None = None
        self._stopped = False

    def record_failure(self, locker_id: int) -> bool:
        """Record a failed pickup attempt. Returns True if this call triggers a new lockout."""
        now = self._clock()
        window = self._policy.window_seconds
        with self._lock:
            attempts = [t for t in self._attempts.get(locker_id, []) if now - t < window]
            attempts.append(now)
            self._attempts[locker_id] = attempts
            self._last_seen[locker_id] = now
            if len(attempts) >= self._policy.max_attempts:
                self._locked_until[locker_id] = now + self._policy.lockout_minutes * 60
                return True
            return False

    def status(self, locker_id: int) -> LockoutStatus | None:
        """Current lockout status for a locker, or None if it is not locked out."""
        with self._lock:
            until = self._locked_until.get(locker_id)
        if until is None:
            return None
        remaining = seconds_remaining(until)
        if remaining <= 0:
            return None
        locked_until_at = datetime.now(UTC) + timedelta(seconds=remaining)
        return LockoutStatus(retry_after_seconds=remaining, locked_until=locked_until_at)

    def clear(self, locker_id: int) -> None:
        """Reset a locker's failed-attempt state: a successful pickup or a courier override."""
        with self._lock:
            self._attempts.pop(locker_id, None)
            self._locked_until.pop(locker_id, None)
            self._last_seen.pop(locker_id, None)

    def alert(self, locker_id: int) -> None:
        """Page ops once for a newly triggered lockout, with a bounded retry."""
        current = self.status(locker_id)
        if current is None:
            return
        for attempt in range(1, _ALERT_ATTEMPTS + 1):
            try:
                self._notifier.notify(locker_id, current.locked_until)
                return
            except AlertTransientError as exc:
                log.warning(
                    "ops alert attempt %d/%d for locker %s failed: %s",
                    attempt,
                    _ALERT_ATTEMPTS,
                    locker_id,
                    exc,
                )
        log.error(
            "ops alert for locker %s could not be delivered after %d attempts",
            locker_id,
            _ALERT_ATTEMPTS,
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="lockout-sweeper", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stopped = True
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stopped:
            time.sleep(self._policy.sweep_seconds)
            self._sweep()

    def _sweep(self) -> None:
        """Expire lockouts that are over and drop attempt history nobody has touched in a while."""
        now = self._clock()
        window = self._policy.window_seconds
        with self._lock:
            for lid in [lid for lid, until in self._locked_until.items() if now >= until]:
                del self._locked_until[lid]
            for lid in list(self._last_seen):
                if lid not in self._locked_until and now - self._last_seen[lid] > window:
                    self._attempts.pop(lid, None)
                    self._last_seen.pop(lid, None)
