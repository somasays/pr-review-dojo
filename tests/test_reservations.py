from datetime import UTC, datetime, timedelta

from app.services.reservations import ReservationCache


def test_reserve_and_release_tracks_held_units():
    cache = ReservationCache()
    hold = cache.reserve("GADGET", 2, stock=5)
    assert hold is not None
    assert hold.sku == "GADGET"
    assert cache.available("GADGET", 5) == 3

    assert cache.release(hold.token) is True
    assert cache.available("GADGET", 5) == 5
    assert cache.release(hold.token) is False


def test_reserve_refuses_more_than_the_free_stock():
    cache = ReservationCache()
    assert cache.reserve("GADGET", 4, stock=5) is not None
    # Exactly the remaining unit still succeeds; one more than that does not.
    assert cache.reserve("GADGET", 1, stock=5) is not None
    assert cache.reserve("GADGET", 1, stock=5) is None
    assert cache.available("GADGET", 5) == 0


def test_expire_drops_holds_past_their_ttl():
    cache = ReservationCache(ttl=timedelta(minutes=10))
    hold = cache.reserve("WIDGET", 3, stock=10)
    assert hold is not None

    assert cache.expire(now=datetime.now(tz=UTC)) == 0
    # The instant a hold expires at counts as expired, not one tick after.
    assert cache.expire(now=hold.expires_at) == 1
    assert cache.available("WIDGET", 10) == 10
    assert list(cache.recently_expired)[-1] == "WIDGET"


def test_create_order_uses_the_reservation_cache(client):
    headers = {"X-API-Key": "customer-test-key"}
    body = {"idempotency_key": "reservation-flow-1", "items": [{"sku": "GADGET", "quantity": 2}]}
    assert client.post("/orders", json=body, headers=headers).status_code == 201

    oversell = {
        "idempotency_key": "reservation-flow-2",
        "items": [{"sku": "GADGET", "quantity": 10}],
    }
    assert client.post("/orders", json=oversell, headers=headers).status_code == 409
