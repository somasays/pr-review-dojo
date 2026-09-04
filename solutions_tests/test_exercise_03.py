"""Hidden tests for exercise 03."""

import ast
import inspect
import textwrap
from decimal import Decimal

import pytest

from app.db.models import Product
from app.domain.money import Money, sum_money
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService, format_refund_audit_row
from app.services.pricing_service import ItemRequest, PricingService


@pytest.fixture
def sender() -> InMemorySender:
    return InMemorySender()


@pytest.fixture
def service(db, sender) -> OrderService:
    return OrderService(db, PricingService(), NotificationService(sender, Settings()))


def _cmd(customer_id: int, key: str = "key-00000001", codes: list[str] | None = None):
    return CreateOrderCommand(
        customer_id=customer_id,
        idempotency_key=key,
        items=[ItemRequest("WIDGET", 2), ItemRequest("GADGET", 1)],
        discount_codes=codes or [],
    )


def _paid_order(db, service, customer_id: int, key: str = "key-00000001", codes=None):
    order = service.create(_cmd(customer_id, key=key, codes=codes))
    db.commit()
    service.mark_paid(order.id)
    return order


def test_second_refund_does_not_restock_again(db, seeded, service, sender):
    """SV-01: a replayed refund must not add the quantities back a second time."""
    order = _paid_order(db, service, seeded["customer"].id)
    service.refund(order.id)
    service.refund(order.id)
    assert seeded["products"]["GADGET"].stock == 5
    assert seeded["products"]["WIDGET"].stock == 100
    refunded = [m for m in sender.sent if m.subject == f"Order {order.id} refunded"]
    assert len(refunded) == 1


def test_refund_dedupe_key_is_stable(db, seeded, service, sender):
    """SV-08: the same refund must produce the same dedupe key every time."""
    svc = NotificationService(sender, Settings(notify_retries=1))
    svc.order_refunded("a@example.com", 8, "10.00 USD", "WIDGET 10.00")
    svc.order_refunded("a@example.com", 8, "10.00 USD", "WIDGET 10.00")
    assert sender.sent[0].dedupe_key == sender.sent[1].dedupe_key
    assert sender.sent[0].dedupe_key == "order-refunded:8"


def test_refund_lines_sum_to_the_charged_amount(db, seeded, service):
    """SV-12: the per line discount shares must add up to the order discount."""
    db.add(Product(sku="THING", name="Thing", unit_price=Decimal("10.03"), stock=10))
    db.commit()
    cmd = CreateOrderCommand(
        customer_id=seeded["customer"].id,
        idempotency_key="key-00000042",
        items=[ItemRequest("WIDGET", 2), ItemRequest("GADGET", 1), ItemRequest("THING", 1)],
        discount_codes=["welcome10"],
    )
    order = service.create(cmd)
    db.commit()
    lines = service.refund_lines(order.id)
    assert len(lines) == 3
    total = sum_money([amount for _sku, amount in lines], order.currency)
    assert total == Money(order.subtotal - order.discount, order.currency)


def test_support_alert_reuses_the_retry_helper():
    """DS-08: the support alert must go through _deliver, not a hand-rolled loop."""
    source = textwrap.dedent(inspect.getsource(NotificationService.notify_support_of_large_refund))
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "_deliver" in calls
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.For)]


def test_refund_audit_row_is_a_pure_function():
    """DS-21: the CSV formatting must be importable and callable without a session."""
    row = format_refund_audit_row(
        9, "a@example.com", Decimal("650.00"), "damaged", [("WIDGET", Money(Decimal("310.00")))]
    )
    assert "9" in row
    assert "WIDGET" in row
    assert "650.00" in row


def test_refund_has_no_boolean_switch():
    """DS-11: refund must not take a boolean that toggles the support alert on and off."""
    sig = inspect.signature(OrderService.refund)
    bool_params = [p for p in sig.parameters.values() if p.annotation is bool]
    assert not bool_params
