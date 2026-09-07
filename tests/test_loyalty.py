from decimal import Decimal

from app.db.models import Order
from app.domain.loyalty import loyalty_credit
from app.domain.money import Money


def _paid_order(total: str, currency: str = "USD") -> Order:
    return Order(
        customer_id=1,
        idempotency_key="history",
        status="paid",
        subtotal=Decimal(total),
        total=Decimal(total),
        currency=currency,
    )


def test_new_customer_gets_no_credit():
    credit = loyalty_credit([], Money.of("80.00"))
    assert credit == Money.zero()


def test_mid_tier_customer_gets_two_percent():
    credit = loyalty_credit([_paid_order("1000.00")], Money.of("100.00"))
    assert credit == Money.of("2.00")


def test_top_tier_customer_gets_five_percent():
    credit = loyalty_credit([_paid_order("3000.00")], Money.of("100.00"))
    assert credit == Money.of("5.00")


def test_credit_is_capped_per_order():
    credit = loyalty_credit([_paid_order("5000.00")], Money.of("5000.00"))
    assert credit == Money.of("50.00")


def test_lifetime_spend_sums_across_orders():
    credit = loyalty_credit([_paid_order("300.00"), _paid_order("300.00")], Money.of("100.00"))
    assert credit == Money.of("2.00")
