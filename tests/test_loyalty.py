from app.domain.loyalty import loyalty_credit
from app.domain.money import Money


def test_new_customer_gets_no_credit():
    credit = loyalty_credit(Money.zero(), Money.of("80.00"))
    assert credit == Money.zero()


def test_mid_tier_customer_gets_two_percent():
    credit = loyalty_credit(Money.of("1000.00"), Money.of("100.00"))
    assert credit == Money.of("2.00")


def test_top_tier_customer_gets_five_percent():
    credit = loyalty_credit(Money.of("3000.00"), Money.of("100.00"))
    assert credit == Money.of("5.00")


def test_credit_is_capped_per_order():
    credit = loyalty_credit(Money.of("5000.00"), Money.of("5000.00"))
    assert credit == Money.of("50.00")


def test_tier_boundary_is_inclusive():
    # A customer landing exactly on the advertised minimum still qualifies.
    assert loyalty_credit(Money.of("500.00"), Money.of("100.00")) == Money.of("2.00")
    assert loyalty_credit(Money.of("2000.00"), Money.of("100.00")) == Money.of("5.00")
    assert loyalty_credit(Money.of("499.99"), Money.of("100.00")) == Money.zero()
