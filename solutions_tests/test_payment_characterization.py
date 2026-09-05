"""Behavior that must survive the rewrite. These pass on both branches."""

from __future__ import annotations

import json

import httpx
import pytest

from app.db.repositories import NotFound
from app.domain.order_state import InvalidTransition
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.payment_service import PaymentDeclined, PaymentGatewayError, PaymentService
from solutions_tests.conftest_helpers import approve, make_order, mock_client


@pytest.fixture
def sender() -> InMemorySender:
    return InMemorySender()


def service(db, sender, handler) -> PaymentService:
    return PaymentService(db, NotificationService(sender, Settings()), client=mock_client(handler))


def test_successful_charge_marks_paid_and_sends_one_receipt(db, seeded, sender):
    order = make_order(db, seeded)

    charged = service(db, sender, approve).charge(order.id, "tok_visa")
    db.commit()

    assert charged.id == order.id
    assert charged.status == "paid"
    assert len(sender.sent) == 1
    message = sender.sent[0]
    assert message.to == "ada@example.com"
    assert message.subject == f"Payment received for order {order.id}"
    assert message.body == "We charged USD 43.58 to your card. Gateway reference ch_123."
    assert message.dedupe_key == f"payment-received:{order.id}"


def test_request_body_and_idempotency_header(db, seeded, sender):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return approve(request)

    order = make_order(db, seeded)
    service(db, sender, handler).charge(order.id, "tok_visa")

    assert str(seen[0].url) == "https://payments.test/charges"
    assert seen[0].headers["Idempotency-Key"] == f"order-{order.id}"
    body = json.loads(seen[0].content)
    assert body == {
        "reference": f"order-{order.id}",
        "amount_cents": 4358,
        "currency": "USD",
        "card_token": "tok_visa",
        "customer_email": "ada@example.com",
        "lines": [{"sku": "WIDGET", "quantity": 2, "unit_price_cents": 1999}],
    }


def test_charging_a_paid_order_does_not_call_the_gateway(db, seeded, sender):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return approve(request)

    order = make_order(db, seeded, status="paid")
    charged = service(db, sender, handler).charge(order.id, "tok_visa")

    assert charged.status == "paid"
    assert calls == []
    assert sender.sent == []


def test_decline_raises_and_leaves_the_order_pending(db, seeded, sender):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(402, json={"reason": "insufficient_funds"})

    order = make_order(db, seeded)
    with pytest.raises(PaymentDeclined) as exc:
        service(db, sender, handler).charge(order.id, "tok_visa")

    assert exc.value.order_id == order.id
    assert exc.value.reason == "insufficient_funds"
    assert order.status == "pending_payment"
    assert sender.sent == []


def test_server_error_raises_gateway_error(db, seeded, sender):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    order = make_order(db, seeded)
    with pytest.raises(PaymentGatewayError):
        service(db, sender, handler).charge(order.id, "tok_visa")

    assert order.status == "pending_payment"


def test_transport_errors_are_retried_then_succeed(db, seeded, sender):
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("gateway unreachable")
        return approve(request)

    order = make_order(db, seeded)
    charged = service(db, sender, handler).charge(order.id, "tok_visa")

    assert len(attempts) == 3
    assert charged.status == "paid"


def test_exhausted_retries_raise_gateway_error(db, seeded, sender):
    attempts: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("gateway unreachable")

    order = make_order(db, seeded)
    with pytest.raises(PaymentGatewayError):
        service(db, sender, handler).charge(order.id, "tok_visa")

    assert len(attempts) == 3
    assert order.status == "pending_payment"
    assert sender.sent == []


def test_status_that_cannot_move_to_paid_raises_before_charging(db, seeded, sender):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return approve(request)

    order = make_order(db, seeded, status="shipped")
    with pytest.raises(InvalidTransition):
        service(db, sender, handler).charge(order.id, "tok_visa")

    assert calls == []


def test_unknown_order_raises_not_found(db, seeded, sender):
    with pytest.raises(NotFound):
        service(db, sender, approve).charge(9999, "tok_visa")
