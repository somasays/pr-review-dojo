"""Covers the Major: redemption must never leave the card balance negative."""

from app.domain.gift_card import redeem
from app.domain.money import Money


def test_partial_redemption_drains_the_card_to_zero_not_negative():
    result = redeem(Money.of("50.00"), Money.of("30.00"))

    assert result.redeemed == Money.of("30.00")
    assert result.remaining_charge == Money.of("20.00")
    assert result.remaining_balance == Money.zero()
    assert not result.remaining_balance.is_negative()
