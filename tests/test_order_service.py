from decimal import Decimal

import pytest

from app.domain.order_state import InvalidTransition
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import InsufficientStock, ItemRequest, PricingService


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


def test_create_prices_and_decrements_stock(db, seeded, service):
    c = seeded["customer"]
    order = service.create(_cmd(c.id, codes=["welcome10"]))
    db.commit()
    assert order.status == "pending_payment"
    assert order.subtotal == Decimal("159.98")
    assert order.discount == Decimal("16.00")
    assert order.total == Decimal("154.42")
    assert order.discount_code == "WELCOME10"
    assert seeded["products"]["GADGET"].stock == 4


def test_create_is_idempotent(db, seeded, service):
    c = seeded["customer"]
    first = service.create(_cmd(c.id))
    db.commit()
    again = service.create(_cmd(c.id))
    assert again.id == first.id
    assert seeded["products"]["GADGET"].stock == 4


def test_insufficient_stock(db, seeded, service):
    c = seeded["customer"]
    cmd = CreateOrderCommand(c.id, "key-00000009", [ItemRequest("GIZMO", 1)], [])
    with pytest.raises(InsufficientStock):
        service.create(cmd)


def test_lifecycle_notifications(db, seeded, service, sender):
    c = seeded["customer"]
    order = service.create(_cmd(c.id))
    db.commit()
    service.mark_paid(order.id)
    service.mark_paid(order.id)  # idempotent, no second email
    service.ship(order.id)
    service.deliver(order.id)
    assert [m.dedupe_key for m in sender.sent] == [
        f"order-confirmed:{order.id}",
        f"order-shipped:{order.id}",
    ]
    assert order.status == "delivered"


def test_cancel_restores_stock_and_blocks_after_payment(db, seeded, service, sender):
    c = seeded["customer"]
    order = service.create(_cmd(c.id))
    db.commit()
    service.cancel(order.id)
    assert seeded["products"]["GADGET"].stock == 5
    assert sender.sent[-1].dedupe_key == f"order-cancelled:{order.id}"

    paid = service.create(_cmd(c.id, key="key-00000002"))
    db.commit()
    service.mark_paid(paid.id)
    with pytest.raises(InvalidTransition):
        service.cancel(paid.id)


def test_refund_restores_stock_and_emails(db, seeded, service, sender):
    c = seeded["customer"]
    order = service.create(_cmd(c.id, key="key-00000003"))
    db.commit()
    service.mark_paid(order.id)
    service.refund(order.id, reason="damaged in transit")
    assert order.status == "refunded"
    assert seeded["products"]["GADGET"].stock == 5
    assert sender.sent[-1].subject == f"Order {order.id} refunded"
    assert sender.sent[-1].dedupe_key.startswith(f"order-refunded:{order.id}")
    assert "damaged in transit" in sender.sent[-1].body


def test_refund_needs_a_paid_order(db, seeded, service):
    c = seeded["customer"]
    order = service.create(_cmd(c.id, key="key-00000004"))
    db.commit()
    with pytest.raises(InvalidTransition):
        service.refund(order.id)


def test_refund_lines_cover_every_item(db, seeded, service):
    c = seeded["customer"]
    order = service.create(_cmd(c.id, key="key-00000005", codes=["welcome10"]))
    db.commit()
    lines = service.refund_lines(order.id)
    assert [sku for sku, _ in lines] == ["WIDGET", "GADGET"]
    assert str(lines[1][1]) == "112.00 USD"


def test_flag_large_refund_only_above_the_threshold(db, seeded, service):
    c = seeded["customer"]
    order = service.create(_cmd(c.id, key="key-00000006"))
    order.total = Decimal("50")
    db.commit()
    assert service.flag_large_refund(order.id) is None

    order.total = Decimal("5000")
    db.commit()
    row = service.flag_large_refund(order.id)
    assert row is not None
    assert "5000" in row
