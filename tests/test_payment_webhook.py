from decimal import Decimal

import pytest

from app.services.config import Settings, get_settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.payments import AmountMismatch, InMemoryGateway, PaymentEvent
from app.services.pricing_service import ItemRequest, PricingService
from conftest import CUSTOMER_KEY

H = {"X-API-Key": CUSTOMER_KEY}


@pytest.fixture
def sender() -> InMemorySender:
    return InMemorySender()


@pytest.fixture
def gateway() -> InMemoryGateway:
    return InMemoryGateway()


@pytest.fixture
def service(db, sender, gateway) -> OrderService:
    return OrderService(db, PricingService(), NotificationService(sender, Settings()), gateway)


@pytest.fixture
def webhook_secret(monkeypatch):
    monkeypatch.setenv("PAYMENT_WEBHOOK_SECRET", "whsec-test")
    get_settings.cache_clear()
    yield "whsec-test"
    get_settings.cache_clear()


def _order(service, db, customer_id, key="key-00000010"):
    order = service.create(CreateOrderCommand(customer_id, key, [ItemRequest("WIDGET", 2)], []))
    db.commit()
    return order


def test_webhook_event_pays_the_order(db, seeded, service, sender, gateway):
    order = _order(service, db, seeded["customer"].id)
    event = PaymentEvent(order.id, "pi_100", order.total, order.currency)
    paid = service.apply_payment_event(event)
    assert paid.status == "paid"
    assert len(gateway.charges) == 1
    assert sender.sent[-1].dedupe_key == f"order-confirmed:{order.id}"


def test_webhook_event_is_replay_safe(db, seeded, service, sender, gateway):
    order = _order(service, db, seeded["customer"].id)
    event = PaymentEvent(order.id, "pi_101", order.total, order.currency)
    service.apply_payment_event(event)
    service.apply_payment_event(event)
    assert len(gateway.charges) == 1
    assert [m.dedupe_key for m in sender.sent] == [f"order-confirmed:{order.id}"]


def test_webhook_event_rejects_a_different_amount(db, seeded, service, gateway):
    order = _order(service, db, seeded["customer"].id)
    bad = PaymentEvent(order.id, "pi_102", order.total - Decimal("1.00"), order.currency)
    with pytest.raises(AmountMismatch):
        service.apply_payment_event(bad)
    assert order.status == "pending_payment"
    assert gateway.charges == []


def test_webhook_endpoint_requires_the_shared_secret(client, webhook_secret):
    created = client.post(
        "/orders",
        json={"idempotency_key": "key-00000011", "items": [{"sku": "WIDGET", "quantity": 1}]},
        headers=H,
    ).json()
    body = {
        "order_id": created["id"],
        "provider_ref": "pi_200",
        "amount": created["total"],
        "currency": created["currency"],
    }
    assert client.post("/payments/webhook", json=body).status_code == 401
    ok = client.post("/payments/webhook", json=body, headers={"X-Webhook-Secret": webhook_secret})
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "paid"
