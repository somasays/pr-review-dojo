"""Payment gateway client and the webhook events the provider sends us.

The gateway is injected so tests and local development can swap it out. A
capture is keyed by the order it belongs to, so a repeat is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from app.domain.money import Money


class PaymentDeclined(Exception):
    """The provider refused the capture. Repeating it will not help."""


class AmountMismatch(Exception):
    """The webhook amount does not match what the order says it costs."""

    def __init__(self, order_id: int, expected: Money, received: Money) -> None:
        super().__init__(f"order {order_id} costs {expected}, provider sent {received}")
        self.order_id = order_id
        self.expected = expected
        self.received = received


@dataclass(frozen=True)
class PaymentEvent:
    """One `payment.authorized` webhook body after validation."""

    order_id: int
    provider_ref: str
    amount: Decimal
    currency: str = "USD"


class PaymentGateway(Protocol):
    def charge(self, amount: Money, idempotency_key: str | None = None) -> str: ...


@dataclass
class InMemoryGateway:
    """Records captures. Used by tests and local development."""

    charges: list[tuple[str | None, Money]] = field(default_factory=list)

    def charge(self, amount: Money, idempotency_key: str | None = None) -> str:
        if idempotency_key is not None:
            for key, _amount in self.charges:
                if key == idempotency_key:
                    return f"cap_{idempotency_key}"
        self.charges.append((idempotency_key, amount))
        return f"cap_{idempotency_key or len(self.charges)}"


GATEWAYS: dict[str, type[PaymentGateway]] = {"memory": InMemoryGateway}


def create_gateway(kind: str = "memory") -> PaymentGateway:
    """Build the configured gateway. Only the in-memory stub exists today."""
    return GATEWAYS[kind]()
