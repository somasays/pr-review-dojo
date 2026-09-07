"""Covers the Major: a customer exactly at a tier's minimum lifetime spend
must get that tier's rate, not the one below it."""

from decimal import Decimal

from app.db.models import Order
from app.domain.order_state import OrderStatus
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import ItemRequest, PricingService


def test_customer_at_tier_boundary_gets_the_higher_rate(db, seeded):
    c = seeded["customer"]
    history = Order(
        customer_id=c.id,
        idempotency_key="history",
        status=OrderStatus.PAID,
        currency="USD",
        subtotal=Decimal("500.00"),
        total=Decimal("500.00"),
    )
    db.add(history)
    db.commit()

    sender = InMemorySender()
    service = OrderService(db, PricingService(), NotificationService(sender, Settings()))
    cmd = CreateOrderCommand(
        customer_id=c.id,
        idempotency_key="key-00000002",
        items=[ItemRequest("WIDGET", 1)],
        discount_codes=[],
    )
    order = service.create(cmd)
    db.commit()

    assert order.loyalty_credit == Decimal("0.40")
