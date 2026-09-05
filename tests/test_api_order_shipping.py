from datetime import UTC, datetime

import pytest

from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import ItemRequest, PricingService
from conftest import ADMIN_KEY, CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}
A = {"X-API-Key": ADMIN_KEY}


@pytest.fixture
def service(db) -> OrderService:
    return OrderService(db, PricingService(), NotificationService(InMemorySender(), Settings()))


def _create_and_pay(client, key: str = "ship-00000001") -> int:
    body = {"idempotency_key": key, "items": [{"sku": "WIDGET", "quantity": 1}]}
    order_id = client.post("/orders", json=body, headers=H).json()["id"]
    client.post(f"/orders/{order_id}/pay", headers=A)
    return int(order_id)


def test_ship_records_tracking_id(client):
    order_id = _create_and_pay(client)
    r = client.post(
        f"/orders/{order_id}/ship", json={"tracking_id": "1Z999AA10123456784"}, headers=A
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "shipped"
    assert body["tracking_id"] == "1Z999AA10123456784"
    assert body["shipped_at"] is not None


def test_ship_without_a_body_still_works(client):
    order_id = _create_and_pay(client, key="ship-00000002")
    body = client.post(f"/orders/{order_id}/ship", headers=A).json()
    assert body["status"] == "shipped"
    assert body["tracking_id"] == ""
    assert body["shipped_at"] is not None


def test_tracking_id_is_bounded(client):
    order_id = _create_and_pay(client, key="ship-00000003")
    r = client.post(f"/orders/{order_id}/ship", json={"tracking_id": "x" * 200}, headers=A)
    assert r.status_code == 422


def test_order_list_exposes_shipping_fields(client):
    order_id = _create_and_pay(client, key="ship-00000004")
    client.post(f"/orders/{order_id}/ship", json={"tracking_id": "TRACK-1"}, headers=A)
    page = client.get("/orders", headers=H).json()
    shipped = [o for o in page["items"] if o["id"] == order_id]
    assert shipped and shipped[0]["tracking_id"] == "TRACK-1"
    assert "customer_id" not in shipped[0]


def test_reshipping_keeps_the_first_shipment(db, seeded, service):
    cmd = CreateOrderCommand(
        customer_id=seeded["customer"].id,
        idempotency_key="ship-00000005",
        items=[ItemRequest("WIDGET", 1)],
        discount_codes=[],
    )
    order = service.create(cmd)
    db.commit()
    service.mark_paid(order.id)
    service.ship(order.id, tracking_id="FIRST")
    first_time = order.shipped_at
    service.ship(order.id, tracking_id="SECOND")
    assert order.tracking_id == "FIRST"
    assert order.shipped_at == first_time


def test_shipped_at_is_utc(db, seeded, service):
    cmd = CreateOrderCommand(
        customer_id=seeded["customer"].id,
        idempotency_key="ship-00000006",
        items=[ItemRequest("WIDGET", 1)],
        discount_codes=[],
    )
    order = service.create(cmd)
    db.commit()
    service.mark_paid(order.id)
    before = datetime.now(tz=UTC)
    service.ship(order.id, tracking_id="TRACK-2")
    assert order.shipped_at is not None
    assert order.shipped_at.tzinfo is not None
    assert order.shipped_at >= before
