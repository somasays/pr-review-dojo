"""Gift card redemption at checkout.

Pure functions over Money. No IO, no database. The service layer looks up
the card and persists the result; this module only decides the split.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.money import Money


@dataclass(frozen=True, slots=True)
class Redemption:
    """The result of redeeming a gift card against an order total."""

    redeemed: Money
    remaining_charge: Money
    remaining_balance: Money


def redeem(total: Money, balance: Money, *, min_remaining: Money | None = None) -> Redemption:
    """Split `total` between the gift card balance and what is still owed.

    Redeems at most the card's balance and at most the order total, so the
    card never covers more than the order costs and the remaining charge
    never goes below zero. Redemption is partial when the balance is smaller
    than the total: the card is drawn down and the rest is still due.

    `min_remaining` lets a caller require the card to keep a minimum balance
    after redemption; unset, the card can be drawn down to zero.
    """
    if total.is_zero() or balance.is_zero():
        return Redemption(
            redeemed=Money.zero(total.currency),
            remaining_charge=total,
            remaining_balance=balance,
        )
    redeemed = total if total.amount <= balance.amount else balance
    remaining_charge = total - redeemed
    remaining_balance = balance - redeemed
    return Redemption(
        redeemed=redeemed, remaining_charge=remaining_charge, remaining_balance=remaining_balance
    )
