"""Payment gateway port and a local stand-in.

The gateway is keyed by the idempotency key we send with each call, so
repeating a charge with the same key never takes the money twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.domain.money import Money


class PaymentDeclined(Exception):
    """The gateway refused the charge. Repeating it will not help."""


class PaymentGateway(Protocol):
    def charge(self, amount: Money, idempotency_key: str) -> str: ...

    def refund(self, charge_id: str, idempotency_key: str) -> None: ...


@dataclass
class InMemoryGateway:
    """Records charges and refunds. Used by tests and local development."""

    charges: dict[str, Money] = field(default_factory=dict)
    refunds: list[str] = field(default_factory=list)

    def charge(self, amount: Money, idempotency_key: str) -> str:
        if amount.is_zero() or amount.is_negative():
            raise PaymentDeclined(f"refusing to charge {amount}")
        self.charges[idempotency_key] = amount
        return f"ch_{idempotency_key}"

    def refund(self, charge_id: str, idempotency_key: str) -> None:
        self.refunds.append(charge_id)
