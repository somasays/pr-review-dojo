from decimal import Decimal

from app.db.models import Product
from app.services.notification import InMemorySender
from app.services.order_service import CreateOrderCommand
from app.services.pricing_service import ItemRequest
from solutions_tests.conftest_helpers import build_service


def test_free_order_confirmation_carries_the_real_order_id(db, seeded):
    sender = InMemorySender()
    service = build_service(db, sender=sender)
    db.add(Product(sku="FREEBIE", name="Freebie", unit_price=Decimal("0"), stock=10))
    db.commit()

    order = service.create(
        CreateOrderCommand(seeded["customer"].id, "key-00000041", [ItemRequest("FREEBIE", 1)], [])
    )
    db.commit()

    assert order.id is not None
    assert sender.sent[0].dedupe_key == f"order-confirmed:{order.id}"
