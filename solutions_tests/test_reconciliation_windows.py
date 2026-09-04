"""Hidden test for the settlement window on OrderService."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.payments import InMemoryGateway
from app.services.pricing_service import ItemRequest, PricingService


def _service(db):
    return OrderService(
        db, PricingService(), NotificationService(InMemorySender(), Settings()), InMemoryGateway()
    )


def test_created_on_day_uses_the_utc_day_of_the_timestamp(db, seeded):
    service = _service(db)
    order = service.create(
        CreateOrderCommand(seeded["customer"].id, "key-00000031", [ItemRequest("WIDGET", 1)], [])
    )
    order.created_at = datetime(2026, 8, 2, 6, 0, tzinfo=UTC)
    db.commit()
    local_evening = datetime(2026, 8, 1, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    assert [o.id for o in service.created_on_day(local_evening)] == [order.id]
