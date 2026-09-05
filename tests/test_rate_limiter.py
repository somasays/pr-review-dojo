from __future__ import annotations

from app.services.rate_limiter import RateLimiter, RateLimitPolicy, seconds_until_reset


def _limiter(limit: int = 3, window: int = 60) -> RateLimiter:
    return RateLimiter(RateLimitPolicy(limit=limit, window_seconds=window))


def test_allows_requests_up_to_the_limit() -> None:
    limiter = _limiter(limit=3)
    decisions = [limiter.hit("key-a") for _ in range(3)]
    assert [d.allowed for d in decisions] == [True, True, True]
    assert [d.remaining for d in decisions] == [2, 1, 0]


def test_blocks_the_request_after_the_limit() -> None:
    limiter = _limiter(limit=2)
    limiter.hit("key-a")
    limiter.hit("key-a")
    decision = limiter.hit("key-a")
    assert decision.allowed is False
    assert decision.remaining == 0
    assert decision.retry_after > 0


def test_keys_are_counted_independently() -> None:
    limiter = _limiter(limit=1)
    assert limiter.hit("key-a").allowed is True
    assert limiter.hit("key-b").allowed is True
    assert limiter.hit("key-a").allowed is False


def test_window_restarts_once_it_has_elapsed() -> None:
    limiter = _limiter(limit=1, window=0)
    assert limiter.hit("key-a").allowed is True
    assert limiter.hit("key-a").allowed is True


def test_sweep_drops_keys_older_than_two_windows(monkeypatch) -> None:
    limiter = _limiter(limit=5, window=1)
    clock = [1_000.0]
    monkeypatch.setattr("app.services.rate_limiter.time.monotonic", lambda: clock[0])
    limiter.hit("stale")
    clock[0] += 10
    limiter.hit("fresh")
    assert limiter.sweep() == 1
    assert limiter.snapshot() == {"fresh": 1}


def test_rate_limit_headers_are_returned(client) -> None:
    from conftest import CUSTOMER_KEY

    response = client.post(
        "/orders",
        headers={"X-API-Key": CUSTOMER_KEY},
        json={"idempotency_key": "rate-limit-1", "items": [{"sku": "WIDGET", "quantity": 1}]},
    )
    assert response.status_code == 201
    assert response.headers["X-RateLimit-Limit"] == "100"
    assert int(response.headers["X-RateLimit-Remaining"]) < 100


def test_seconds_until_reset_counts_down_to_zero() -> None:
    assert seconds_until_reset(1_000.0, 60, now=1_000.0) == 60
    assert seconds_until_reset(1_000.0, 60, now=1_090.0) == 0


def test_rate_limit_usage_report(client) -> None:
    from conftest import ADMIN_KEY, CUSTOMER_KEY

    client.post(
        "/orders",
        headers={"X-API-Key": CUSTOMER_KEY},
        json={"idempotency_key": "rate-limit-2", "items": [{"sku": "WIDGET", "quantity": 1}]},
    )
    response = client.get("/reports/rate-limits", headers={"X-API-Key": ADMIN_KEY})
    assert response.status_code == 200
    rows = response.json()
    assert any(row["hits"] >= 1 and row["usage"].endswith("%") for row in rows)
