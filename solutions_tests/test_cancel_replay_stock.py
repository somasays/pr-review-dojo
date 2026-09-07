"""Covers the Blocker: a replayed cancel must not restock twice."""

from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.pricing_service import ItemRequest, PricingService


def _cmd(customer_id: int, key: str = "key-00000001"):
    return CreateOrderCommand(
        customer_id=customer_id,
        idempotency_key=key,
        items=[ItemRequest("WIDGET", 2), ItemRequest("GADGET", 1)],
        discount_codes=[],
    )


def test_cancel_replay_does_not_restock_twice(db, seeded):
    sender = InMemorySender()
    service = OrderService(db, PricingService(), NotificationService(sender, Settings()))
    c = seeded["customer"]
    order = service.create(_cmd(c.id))
    db.commit()

    service.cancel(order.id)
    service.cancel(order.id)

    assert seeded["products"]["GADGET"].stock == 5
    cancelled = [m for m in sender.sent if m.dedupe_key == f"order-cancelled:{order.id}"]
    assert len(cancelled) == 1
