"""Hidden tests for the capture path in OrderService.mark_paid."""

import pytest

from app.domain.money import Money
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import CreateOrderCommand, OrderService
from app.services.payments import PaymentDeclined
from app.services.pricing_service import ItemRequest, PricingService


class DecliningGateway:
    def charge(self, amount: Money, idempotency_key: str | None = None) -> str:
        raise PaymentDeclined("card declined")


class FlakyGateway:
    """Dedupes on the idempotency key and times out once after the first charge."""

    def __init__(self) -> None:
        self.charges: dict[str, Money] = {}
        self.timed_out = False

    def charge(self, amount: Money, idempotency_key: str | None = None) -> str:
        if idempotency_key is not None and idempotency_key in self.charges:
            return f"cap_{idempotency_key}"
        self.charges[idempotency_key or f"anon-{len(self.charges)}"] = amount
        if not self.timed_out:
            self.timed_out = True
            raise TimeoutError("gateway timed out reading the response")
        return "cap_ok"


def _service(db, sender, gateway):
    return OrderService(db, PricingService(), NotificationService(sender, Settings()), gateway)


def _create(service, db, customer_id, key="key-00000021"):
    order = service.create(CreateOrderCommand(customer_id, key, [ItemRequest("WIDGET", 2)], []))
    db.commit()
    return order


def test_declined_capture_does_not_pay_the_order(db, seeded):
    sender = InMemorySender()
    service = _service(db, sender, DecliningGateway())
    order = _create(service, db, seeded["customer"].id)
    with pytest.raises(PaymentDeclined):
        service.mark_paid(order.id)
    assert order.status == "pending_payment"
    assert sender.sent == []


def test_capture_is_keyed_so_a_timeout_does_not_bill_twice(db, seeded):
    sender = InMemorySender()
    gateway = FlakyGateway()
    service = _service(db, sender, gateway)
    order = _create(service, db, seeded["customer"].id, key="key-00000022")
    service.mark_paid(order.id)
    assert len(gateway.charges) == 1
    assert order.status == "paid"
