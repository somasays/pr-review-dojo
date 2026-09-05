from app.services.config import Settings
from app.services.notification import (
    InMemorySender,
    NotificationFlusher,
    NotificationService,
)


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


def test_service_with_a_flusher_queues_instead_of_sending():
    sender = InMemorySender()
    flusher = NotificationFlusher(sender, Settings(notify_retries=1))
    svc = NotificationService(sender, Settings(), flusher)

    svc.order_confirmed("ada@example.com", 11, "12.00 USD")

    assert sender.sent == []
    assert flusher.pending_count() == 1


def test_flush_sends_the_queued_batch():
    sender = InMemorySender()
    flusher = NotificationFlusher(sender, Settings(notify_retries=1))
    svc = NotificationService(sender, Settings(), flusher)

    svc.order_shipped("ada@example.com", 12)
    flusher.flush()

    # flush() blocks until the batch's sends have all completed, so the
    # result is checked directly instead of polling for it.
    assert sender.sent[0].dedupe_key == "order-shipped:12"
    assert flusher.pending_count() == 0


def test_a_dedupe_key_is_not_sent_twice_in_one_window():
    sender = InMemorySender()
    flusher = NotificationFlusher(sender, Settings(notify_retries=1))
    svc = NotificationService(sender, Settings(), flusher)

    svc.order_shipped("ada@example.com", 13)
    flusher.flush()
    assert len(sender.sent) == 1

    svc.order_shipped("ada@example.com", 13)
    flusher.flush()

    assert len(sender.sent) == 1
