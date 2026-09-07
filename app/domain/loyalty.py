"""Loyalty credit: a small discount for returning customers at checkout.

Pure functions over `Money`. The credit is tiered by how much a customer
has spent on paid orders, capped so a single order never gets more than
`MAX_CREDIT_PER_ORDER`, and applied after discount codes but before tax
(see `OrderService.create`).
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.money import Money

MAX_CREDIT_PER_ORDER = Money.of("50.00")

# (minimum lifetime spend, credit rate) pairs, highest tier first.
LOYALTY_TIERS: tuple[tuple[Money, Decimal], ...] = (
    (Money.of("2000.00"), Decimal("5")),
    (Money.of("500.00"), Decimal("2")),
    (Money.of("0.00"), Decimal("0")),
)


def _rate_for(lifetime_spend: Money) -> Decimal:
    for minimum, rate in LOYALTY_TIERS:
        if minimum <= lifetime_spend:
            return rate
    return Decimal("0")


def loyalty_credit(lifetime_spend: Money, taxable: Money) -> Money:
    """Return the loyalty credit for an order given the customer's lifetime spend.

    `taxable` is the order's subtotal after discount codes, before tax. The
    credit is a percentage of that amount, tiered by `lifetime_spend`, capped
    at `MAX_CREDIT_PER_ORDER`.
    """
    rate = _rate_for(lifetime_spend)
    credit = taxable.percent(rate)
    if MAX_CREDIT_PER_ORDER < credit:
        return MAX_CREDIT_PER_ORDER
    return credit
