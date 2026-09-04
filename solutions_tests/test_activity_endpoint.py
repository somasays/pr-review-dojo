"""Hidden tests for exercise 07: the endpoint paths the shipped test never exercised."""

from datetime import UTC, datetime

from conftest import ADMIN_KEY, CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}
A = {"X-API-Key": ADMIN_KEY}


def test_order_activity_with_explicit_window(client):
    """TR-01: the ?window= path (the one that trips LG-04) had no test at all."""
    body = {"idempotency_key": "key-00000010", "items": [{"sku": "WIDGET", "quantity": 1}]}
    client.post("/orders", json=body, headers=H)
    today = datetime.now(UTC).date().isoformat()
    r = client.get(f"/reports/orders/activity?window={today}", headers=A)
    assert r.status_code == 200
    out = r.json()
    assert out["orders"] == 1
    assert out["active_days"] == 1


def test_order_activity_with_no_orders_in_window(client):
    """TR-01: a window with no orders (the one that trips LG-06) had no test at all."""
    r = client.get("/reports/orders/activity?days=7", headers=A)
    assert r.status_code == 200
    out = r.json()
    assert out["orders"] == 0
    assert out["active_days"] == 0
    assert out["first_active_day"] is None
    assert out["last_active_day"] is None
