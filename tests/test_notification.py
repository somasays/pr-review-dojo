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


def test_refund_email_carries_the_breakdown():
    sender = InMemorySender()
    svc = NotificationService(sender, Settings(notify_retries=2))
    svc.order_refunded("a@example.com", 9, "154.42", "WIDGET 31.98, GADGET 112.00")
    assert sender.sent[0].subject == "Order 9 refunded"
    assert "154.42" in sender.sent[0].body
    assert "GADGET 112.00" in sender.sent[0].body
