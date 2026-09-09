from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService


def test_sends_with_dedupe_key():
    sender = InMemorySender()
    svc = NotificationService(sender, Settings(notify_retries=2))
    svc.order_confirmed("a@example.com", 7, "12.00 USD")
    assert len(sender.sent) == 1
    assert sender.sent[0].dedupe_key == "order-confirmed:7"
    assert "12.00 USD" in sender.sent[0].body


def test_retries_transient_gateway_errors():
    sender = InMemorySender(fail_times=2)
    svc = NotificationService(sender, Settings(notify_retries=3))
    svc.order_shipped("a@example.com", 8)
    assert [m.subject for m in sender.sent] == ["Order 8 shipped"]


def test_warehouse_digest_lists_every_order():
    sender = InMemorySender()
    svc = NotificationService(sender, Settings(notify_retries=2))
    svc.warehouse_digest([1, 2])
    assert [m.dedupe_key for m in sender.sent] == ["warehouse-digest:1", "warehouse-digest:2"]
    assert sender.sent[0].to == "warehouse@example.com"
