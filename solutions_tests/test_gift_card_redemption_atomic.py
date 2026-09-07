"""Covers the Major: redeeming a gift card must roll back with the order.

If the caller's transaction is rolled back after `create()` returns (a later
step fails, or the request dependency rolls back for any other reason), the
gift card debit must go with it. It must not already be durable on its own.
"""

from decimal import Decimal

from app.db.models import GiftCard
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import ItemRequest, PricingService


def test_gift_card_debit_rolls_back_with_the_order(db, seeded):
    card = GiftCard(code="ROLLBACK1", balance=Decimal("100.00"), currency="USD")
    db.add(card)
    db.commit()

    service = OrderService(db, PricingService(), NotificationService(InMemorySender(), Settings()))
    cmd = CreateOrderCommand(
        customer_id=seeded["customer"].id,
        idempotency_key="rollback-key-001",
        items=[ItemRequest("WIDGET", 1)],
        discount_codes=[],
        gift_card_code="ROLLBACK1",
    )
    service.create(cmd)

    db.rollback()

    refreshed = db.get(GiftCard, card.id)
    assert refreshed.balance == Decimal("100.00")
