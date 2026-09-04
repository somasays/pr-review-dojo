import pytest

from app.services.retry import RetryExhausted, RetryPolicy, retry


def test_succeeds_after_transient_failures():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("nope")
        return "ok"

    assert retry(flaky, RetryPolicy(attempts=3), sleep=lambda _s: None) == "ok"
    assert len(calls) == 3


def test_exhausts_and_wraps_last_error():
    def always():
        raise TimeoutError("slow")

    with pytest.raises(RetryExhausted) as info:
        retry(always, RetryPolicy(attempts=2), sleep=lambda _s: None)
    assert isinstance(info.value.last, TimeoutError)
    assert info.value.attempts == 2


def test_non_retryable_propagates_immediately():
    calls = []

    def bad():
        calls.append(1)
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        retry(bad, RetryPolicy(attempts=5), sleep=lambda _s: None)
    assert len(calls) == 1


def test_backoff_schedule():
    p = RetryPolicy(attempts=5, backoff_seconds=0.1, multiplier=2, max_backoff_seconds=0.35)
    assert [p.delay(i) for i in range(1, 6)] == [0.0, 0.1, 0.2, 0.35, 0.35]
    slept = []
    retry(lambda: None, p, sleep=slept.append)
    assert slept == []
