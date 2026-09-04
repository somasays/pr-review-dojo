import pytest

from app.services.config import Settings
from app.services.notification import Message, NotificationService


class ThirdSendFails:
    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.calls = 0

    def send(self, message: Message) -> None:
        self.calls += 1
        if self.calls == 3:
            raise ConnectionError("gateway unavailable")
        self.sent.append(message)


class BadRecipient:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, message: Message) -> None:
        self.calls += 1
        raise ValueError("invalid recipient")


def _messages(count: int) -> list[Message]:
    return [
        Message(to="a@example.com", subject=f"note {i}", body="hello", dedupe_key=f"k{i}")
        for i in range(count)
    ]


def test_send_many_does_not_resend_what_already_went_out():
    stub = ThirdSendFails()
    svc = NotificationService(stub, Settings(notify_retries=3))

    svc.send_many(_messages(4))

    assert [m.dedupe_key for m in stub.sent] == ["k0", "k1", "k2", "k3"]


def test_permanent_errors_are_not_retried():
    stub = BadRecipient()
    svc = NotificationService(stub, Settings(notify_retries=3))

    with pytest.raises(ValueError):
        svc.order_confirmed("nope", 1, "1.00")

    assert stub.calls == 1
