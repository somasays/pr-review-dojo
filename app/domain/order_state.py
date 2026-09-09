"""Order status state machine.

Orders move through a fixed set of states. Every transition goes through
`transition()` so the invariants live in one place.
"""

from __future__ import annotations

from enum import StrEnum


class OrderStatus(StrEnum):
    DRAFT = "draft"
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class InvalidTransition(Exception):
    def __init__(self, current: OrderStatus, target: OrderStatus) -> None:
        super().__init__(f"cannot move order from {current} to {target}")
        self.current = current
        self.target = target


TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.DRAFT: frozenset({OrderStatus.PENDING_PAYMENT, OrderStatus.CANCELLED}),
    OrderStatus.PENDING_PAYMENT: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.SHIPPED, OrderStatus.REFUNDED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset({OrderStatus.REFUNDED}),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REFUNDED: frozenset(),
}

TERMINAL = frozenset({OrderStatus.CANCELLED, OrderStatus.REFUNDED, OrderStatus.DELIVERED})


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in TRANSITIONS[current]


def transition(current: OrderStatus, target: OrderStatus) -> OrderStatus:
    """Return the new status or raise InvalidTransition."""
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    return target


def is_terminal(status: OrderStatus) -> bool:
    return status in TERMINAL


def is_cancellable(status: OrderStatus) -> bool:
    return can_transition(status, OrderStatus.CANCELLED)


def is_refundable(status: OrderStatus) -> bool:
    return can_transition(status, OrderStatus.REFUNDED)


def allowed_targets(current: OrderStatus) -> list[OrderStatus]:
    return sorted(TRANSITIONS[current], key=lambda s: s.value)
