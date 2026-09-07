"""Loyalty credit: a small discount for returning customers at checkout.

Pure functions over `Money` and the ORM rows the caller already has. The
credit is tiered by how much a customer has spent on paid orders, capped so
a single order never gets more than `MAX_CREDIT_PER_ORDER`, and applied
after discount codes but before tax (see `OrderService.create`).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.db.models import Order
from app.domain.money import Money, sum_money

MAX_CREDIT_PER_ORDER = Money.of("50.00")

# (minimum lifetime spend, credit rate) pairs, highest tier first.
LOYALTY_TIERS: tuple[tuple[Money, Decimal], ...] = (
    (Money.of("2000.00"), Decimal("5")),
    (Money.of("500.00"), Decimal("2")),
    (Money.of("0.00"), Decimal("0")),
)


def _rate_for(lifetime_spend: Money, tiers: tuple[tuple[Money, Decimal], ...]) -> Decimal:
    for minimum, rate in tiers:
        if minimum <= lifetime_spend:
            return rate
    return Decimal("0")


def loyalty_credit(
    paid_orders: Sequence[Order],
    taxable: Money,
    tiers: tuple[tuple[Money, Decimal], ...] = LOYALTY_TIERS,
) -> Money:
    """Return the loyalty credit for an order given the customer's paid history.

    `taxable` is the order's subtotal after discount codes, before tax. The
    credit is a percentage of that amount, tiered by the customer's lifetime
    spend across `paid_orders`, capped at `MAX_CREDIT_PER_ORDER`.
    """
    lifetime_spend = sum_money([Money(o.total, o.currency) for o in paid_orders], taxable.currency)
    rate = _rate_for(lifetime_spend, tiers)
    credit = Money(taxable.amount * rate / Decimal(100), taxable.currency)
    if MAX_CREDIT_PER_ORDER < credit:
        return MAX_CREDIT_PER_ORDER
    return credit
