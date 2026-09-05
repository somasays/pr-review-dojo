"""Structural expectations for the rewrite. These fail on the exercise branch."""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal

from app.db.models import Customer, Order, OrderItem
from app.services import payment_service
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService


def _detached_order() -> Order:
    order = Order(
        id=7,
        customer_id=1,
        idempotency_key="pay-00000001",
        status="pending_payment",
        currency="USD",
        subtotal=Decimal("39.98"),
        discount=Decimal("0.00"),
        tax=Decimal("3.60"),
        total=Decimal("43.58"),
    )
    order.customer = Customer(id=1, email="ada@example.com", name="Ada", region="US-CA")
    order.items = [OrderItem(product_id=1, sku="WIDGET", quantity=2, unit_price=Decimal("19.99"))]
    return order


def test_payload_building_is_a_pure_function():
    """The gateway body can be built with no session and no HTTP client."""
    payload = payment_service.charge_payload(_detached_order(), "tok_visa")

    assert payload == {
        "reference": "order-7",
        "amount_cents": 4358,
        "currency": "USD",
        "card_token": "tok_visa",
        "customer_email": "ada@example.com",
        "lines": [{"sku": "WIDGET", "quantity": 2, "unit_price_cents": 1999}],
    }


def test_gateway_configuration_comes_from_settings():
    fields = Settings.__dataclass_fields__
    for name in (
        "payment_gateway_url",
        "payment_api_key",
        "payment_timeout_seconds",
        "payment_attempts",
    ):
        assert name in fields, f"Settings is missing {name}"


def test_payment_service_does_not_read_the_environment_itself():
    tree = ast.parse(inspect.getsource(payment_service))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    ]
    assert reads == [], "payment_service reads os.environ instead of Settings"


def test_receipt_formatting_lives_in_the_notification_service():
    sender = InMemorySender()
    NotificationService(sender, Settings()).payment_received(
        "ada@example.com", 7, "USD 43.58", "ch_123"
    )

    assert len(sender.sent) == 1
    assert sender.sent[0].subject == "Payment received for order 7"
    assert sender.sent[0].body == "We charged USD 43.58 to your card. Gateway reference ch_123."
    assert sender.sent[0].dedupe_key == "payment-received:7"


def test_charge_is_short_enough_to_read_in_one_screen():
    tree = ast.parse(inspect.getsource(payment_service))
    charge = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "charge"
    )
    length = charge.end_lineno - charge.lineno + 1
    assert length <= 30, f"PaymentService.charge is {length} lines"
