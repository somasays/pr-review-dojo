import pytest

from app.domain.order_state import (
    InvalidTransition,
    OrderStatus,
    allowed_targets,
    is_cancellable,
    is_terminal,
    transition,
)


def test_happy_path():
    s = OrderStatus.DRAFT
    for target in [
        OrderStatus.PENDING_PAYMENT,
        OrderStatus.PAID,
        OrderStatus.SHIPPED,
        OrderStatus.DELIVERED,
    ]:
        s = transition(s, target)
    assert is_terminal(s)


def test_invalid_transitions():
    with pytest.raises(InvalidTransition):
        transition(OrderStatus.SHIPPED, OrderStatus.CANCELLED)
    with pytest.raises(InvalidTransition):
        transition(OrderStatus.CANCELLED, OrderStatus.PAID)
    with pytest.raises(InvalidTransition):
        transition(OrderStatus.PAID, OrderStatus.PAID)


def test_cancellable_and_targets():
    assert is_cancellable(OrderStatus.PENDING_PAYMENT)
    assert not is_cancellable(OrderStatus.PAID)
    assert allowed_targets(OrderStatus.PAID) == [OrderStatus.REFUNDED, OrderStatus.SHIPPED]
