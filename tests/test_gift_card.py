from app.domain.gift_card import redeem
from app.domain.money import Money


def test_redeem_covers_order_when_balance_is_larger():
    result = redeem(Money.of("30.00"), Money.of("50.00"))
    assert result.redeemed == Money.of("30.00")
    assert result.remaining_charge == Money.zero()
    assert result.remaining_balance == Money.of("20.00")


def test_redeem_with_no_balance_leaves_full_charge():
    result = redeem(Money.of("30.00"), Money.zero())
    assert result.redeemed == Money.zero()
    assert result.remaining_charge == Money.of("30.00")
    assert result.remaining_balance == Money.zero()


def test_redeem_with_zero_total_takes_nothing():
    result = redeem(Money.zero(), Money.of("50.00"))
    assert result.redeemed == Money.zero()
    assert result.remaining_balance == Money.of("50.00")


def test_redeem_partial_when_balance_is_smaller():
    result = redeem(Money.of("50.00"), Money.of("30.00"))
    assert result.redeemed == Money.of("30.00")
    assert result.remaining_charge == Money.of("20.00")
    assert result.remaining_balance == Money.zero()
