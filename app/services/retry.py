"""Retry helper for transient failures.

Only retry operations that are safe to repeat. See README on idempotency.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


class RetryExhausted(Exception):
    def __init__(self, attempts: int, last: BaseException) -> None:
        super().__init__(f"gave up after {attempts} attempts: {last!r}")
        self.attempts = attempts
        self.last = last


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    backoff_seconds: float = 0.2
    multiplier: float = 2.0
    max_backoff_seconds: float = 5.0
    retry_on: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)

    def delay(self, attempt: int) -> float:
        """Delay before attempt number `attempt` (1-based; attempt 1 has no delay)."""
        if attempt <= 1:
            return 0.0
        raw = self.backoff_seconds * (self.multiplier ** (attempt - 2))
        return min(raw, self.max_backoff_seconds)


def retry[T](
    fn: Callable[[], T],
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call fn until it succeeds or the policy is exhausted.

    Exceptions not listed in policy.retry_on propagate immediately.
    """
    if policy.attempts < 1:
        raise ValueError("attempts must be at least 1")
    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        delay = policy.delay(attempt)
        if delay:
            sleep(delay)
        try:
            return fn()
        except policy.retry_on as exc:
            last = exc
            log.warning("attempt %d/%d failed: %s", attempt, policy.attempts, exc)
    assert last is not None
    raise RetryExhausted(policy.attempts, last)
