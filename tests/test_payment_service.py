from decimal import Decimal

import httpx
import pytest

from app.db.models import Order, OrderItem
from app.domain.order_state import InvalidTransition
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.payment_service import PaymentDeclined, PaymentService


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://payments.test")


def _approve(request: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json={"id": "ch_123", "status": "captured"})


@pytest.fixture
def sender() -> InMemorySender:
    return InMemorySender()


def _make_order(db, seeded, status: str = "pending_payment") -> Order:
    row = Order(
        customer_id=seeded["customer"].id,
        idempotency_key="pay-00000001",
        status=status,
        currency="USD",
        subtotal=Decimal("39.98"),
        discount=Decimal("0.00"),
        tax=Decimal("3.60"),
        total=Decimal("43.58"),
    )
    row.items = [
        OrderItem(
            product_id=seeded["products"]["WIDGET"].id,
            sku="WIDGET",
            quantity=2,
            unit_price=Decimal("19.99"),
        )
    ]
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def order(db, seeded) -> Order:
    return _make_order(db, seeded)


def _service(db, sender, handler) -> PaymentService:
    return PaymentService(db, NotificationService(sender, Settings()), client=_client(handler))


def test_charge_marks_the_order_paid_and_emails_a_receipt(db, order, sender):
    service = _service(db, sender, _approve)

    charged = service.charge(order.id, "tok_visa")
    db.commit()

    assert charged.status == "paid"
    assert [m.subject for m in sender.sent] == [f"Payment received for order {order.id}"]
    assert sender.sent[0].dedupe_key == f"payment-received:{order.id}"
    assert "ch_123" in sender.sent[0].body


def test_charge_sends_the_order_total_and_lines(db, order, sender):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.append(json.loads(request.content))
        return _approve(request)

    _service(db, sender, handler).charge(order.id, "tok_visa")

    assert seen[0]["amount_cents"] == 4358
    assert seen[0]["currency"] == "USD"
    assert seen[0]["reference"] == f"order-{order.id}"
    assert seen[0]["lines"] == [{"sku": "WIDGET", "quantity": 2, "unit_price_cents": 1999}]


def test_declined_card_raises(db, order, sender):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"reason": "insufficient_funds"})

    with pytest.raises(PaymentDeclined) as exc:
        _service(db, sender, handler).charge(order.id, "tok_visa")

    assert exc.value.reason == "insufficient_funds"
    assert order.status == "pending_payment"
    assert sender.sent == []


def test_charging_an_already_paid_order_is_a_no_op(db, seeded, sender):
    order = _make_order(db, seeded, status="paid")
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _approve(request)

    charged = _service(db, sender, handler).charge(order.id, "tok_visa")

    assert charged.status == "paid"
    assert calls == []
    assert sender.sent == []


def test_charging_an_order_that_cannot_move_to_paid_raises_before_charging(db, seeded, sender):
    order = _make_order(db, seeded, status="shipped")
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _approve(request)

    with pytest.raises(InvalidTransition):
        _service(db, sender, handler).charge(order.id, "tok_visa")

    assert calls == []


def test_charge_sends_the_configured_api_key(db, order, sender):
    seen_headers: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        return _approve(request)

    service = _service(db, sender, handler)
    service.api_key = "sk_test_123"

    service.charge(order.id, "tok_visa")

    assert seen_headers[0]["authorization"] == "Bearer sk_test_123"
