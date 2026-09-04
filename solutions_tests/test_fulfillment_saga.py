import pytest

from app.db.models import Order
from app.services.notification import Message, NotificationError
from app.services.order_service import FulfillmentFailed
from app.services.payment import InMemoryGateway
from solutions_tests.conftest_helpers import build_service, standard_order


class SubjectFailingSender:
    """Records every message except the ones whose subject matches."""

    def __init__(self, fail_on: str) -> None:
        self.sent: list[Message] = []
        self.fail_on = fail_on

    def send(self, message: Message) -> None:
        if self.fail_on in message.subject:
            raise ConnectionError("gateway unavailable")
        self.sent.append(message)


class DeadGateway:
    def charge(self, amount, idempotency_key):  # noqa: ANN001
        raise ConnectionError("payment gateway unreachable")

    def refund(self, charge_id, idempotency_key):  # noqa: ANN001
        raise AssertionError("nothing was charged, so nothing can be refunded")


def test_fulfill_does_not_reserve_stock_that_create_already_reserved(db, seeded):
    service = build_service(db)
    order = service.create(standard_order(seeded["customer"].id))
    db.commit()

    service.fulfill(order.id, "TRACK-1")

    assert order.status == "shipped"
    assert seeded["products"]["GADGET"].stock == 4
    assert seeded["products"]["WIDGET"].stock == 98


def test_compensation_leaves_the_transaction_to_the_caller(db, seeded):
    sender = SubjectFailingSender("cancelled")
    service = build_service(db, sender=sender, gateway=DeadGateway(), retries=1)
    order = service.create(standard_order(seeded["customer"].id))
    db.commit()
    order_id = order.id

    with pytest.raises(NotificationError):
        service.fulfill(order_id, "TRACK-1")
    db.rollback()

    assert db.get(Order, order_id).status == "pending_payment"


def test_a_shipped_order_is_never_reported_as_cancelled(db, seeded):
    sender = SubjectFailingSender("shipped")
    service = build_service(db, sender=sender, gateway=InMemoryGateway(), retries=1)
    order = service.create(standard_order(seeded["customer"].id))
    db.commit()

    with pytest.raises(FulfillmentFailed):
        service.fulfill(order.id, "TRACK-1")

    assert order.status == "shipped"
    assert [m.dedupe_key for m in sender.sent if m.dedupe_key.startswith("order-cancelled:")] == []
