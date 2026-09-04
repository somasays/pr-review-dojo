"""Small builders shared by the hidden tests."""

from __future__ import annotations

from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.payment import InMemoryGateway
from app.services.pricing_service import ItemRequest, PricingService


def build_service(session, sender=None, gateway=None, retries=3):
    return OrderService(
        session,
        PricingService(),
        NotificationService(sender or InMemorySender(), Settings(notify_retries=retries)),
        gateway or InMemoryGateway(),
    )


def standard_order(customer_id: int, key: str = "key-00000021", codes=None):
    return CreateOrderCommand(
        customer_id=customer_id,
        idempotency_key=key,
        items=[ItemRequest("WIDGET", 2), ItemRequest("GADGET", 1)],
        discount_codes=codes or [],
    )
