"""Covers the Major: the credit notification's dedupe key must be stable
across retries, or the gateway sends it more than once."""

from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService


def test_credit_notification_dedupe_key_is_stable():
    sender = InMemorySender()
    svc = NotificationService(sender, Settings(notify_retries=1))
    svc.loyalty_credit_applied("a@example.com", 42, "3.20 USD")
    svc.loyalty_credit_applied("a@example.com", 42, "3.20 USD")
    assert sender.sent[0].dedupe_key == sender.sent[1].dedupe_key == "order-credit:42"
