from datetime import UTC, datetime

from conftest import ADMIN_KEY, CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}
A = {"X-API-Key": ADMIN_KEY}


def test_reports_are_admin_only(client):
    assert client.get("/reports/orders/by-status", headers=H).status_code == 403


def test_by_status_and_recent_total(client):
    body = {"idempotency_key": "key-00000001", "items": [{"sku": "WIDGET", "quantity": 1}]}
    client.post("/orders", json=body, headers=H)
    r = client.get("/reports/orders/by-status", headers=A)
    assert r.json() == [{"status": "pending_payment", "count": 1}]
    t = client.get("/reports/orders/recent-total?days=1", headers=A).json()
    assert t["orders"] == 1
    assert t["total"] == "21.44"


def test_order_activity(client):
    body = {"idempotency_key": "key-00000002", "items": [{"sku": "WIDGET", "quantity": 1}]}
    client.post("/orders", json=body, headers=H)
    r = client.get("/reports/orders/activity?days=7&include_today=true", headers=A)
    assert r.status_code == 200
    out = r.json()
    assert out["orders"] == 1
    assert out["active_days"] == 1
    assert len(out["active_periods"]) == 1
    assert out["first_active_day"] == out["last_active_day"]
    assert out["weekly_orders"][0]["orders"] == 1


def test_order_activity_with_explicit_window(client):
    body = {"idempotency_key": "key-00000003", "items": [{"sku": "WIDGET", "quantity": 1}]}
    client.post("/orders", json=body, headers=H)
    today = datetime.now(UTC).date().isoformat()
    r = client.get(f"/reports/orders/activity?window={today}", headers=A)
    assert r.status_code == 200
    out = r.json()
    assert out["orders"] == 1
    assert out["active_days"] == 1


def test_order_activity_with_no_orders_in_window(client):
    r = client.get("/reports/orders/activity?days=7", headers=A)
    assert r.status_code == 200
    out = r.json()
    assert out["orders"] == 0
    assert out["active_days"] == 0
    assert out["first_active_day"] is None
    assert out["last_active_day"] is None
