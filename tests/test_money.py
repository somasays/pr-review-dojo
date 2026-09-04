from decimal import Decimal

import pytest

from app.domain.money import CurrencyMismatch, Money, sum_money


def test_of_quantizes_half_up():
    assert Money.of("1.005").amount == Decimal("1.01")
    assert Money.of("1.004").amount == Decimal("1.00")


def test_rejects_float():
    with pytest.raises(TypeError):
        Money.of(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money.of("2") * 1.5  # type: ignore[operator]


def test_arithmetic_and_currency_check():
    a = Money.of("10.00")
    b = Money.of("2.50")
    assert (a + b).amount == Decimal("12.50")
    assert (a - b).amount == Decimal("7.50")
    assert (b * 3).amount == Decimal("7.50")
    with pytest.raises(CurrencyMismatch):
        a + Money.of("1", "EUR")


def test_percent_rounds_to_cents():
    assert Money.of("19.99").percent(Decimal("7.25")).amount == Decimal("1.45")


def test_allocate_sums_exactly():
    parts = Money.of("10.00").allocate(3)
    assert [p.amount for p in parts] == [Decimal("3.34"), Decimal("3.33"), Decimal("3.33")]
    assert sum_money(parts).amount == Decimal("10.00")
    with pytest.raises(ValueError):
        Money.of("1").allocate(0)


def test_invalid_currency():
    with pytest.raises(ValueError):
        Money.of("1", "usd")
