from datetime import date
from decimal import Decimal

import pytest

from app.db.models import Product
from app.domain.exchange import ExchangeRate, RateTable, UnknownRate
from app.domain.money import Money

AS_OF = date(2026, 8, 1)
USD_EUR = ExchangeRate("USD", "EUR", Decimal("0.92"), AS_OF)
TABLE = RateTable((USD_EUR,))


def test_rate_validation():
    with pytest.raises(ValueError):
        ExchangeRate("USD", "USD", Decimal("1"), AS_OF)
    with pytest.raises(ValueError):
        ExchangeRate("USD", "EUR", Decimal("0"), AS_OF)


def test_same_currency_is_identity():
    assert TABLE.rate_for("USD", "USD") == Decimal(1)
    amount = Money.of("12.34")
    assert TABLE.convert(amount, "USD") is amount


def test_convert_uses_published_rate():
    assert TABLE.convert(Money.of("100.00"), "EUR") == Money.of("92.00", "EUR")


def test_convert_falls_back_to_the_inverse():
    back = TABLE.convert(Money.of("92.00", "EUR"), "USD")
    assert back == Money.of("100.00")


def test_unknown_pair_is_reported():
    with pytest.raises(UnknownRate) as info:
        TABLE.rate_for("USD", "JPY")
    assert info.value.quote == "JPY"


def test_currencies_lists_both_sides():
    assert TABLE.currencies() == {"USD", "EUR"}


def test_round_down_drops_minor_units():
    assert Money.of("1234.99", "EUR").round_down() == Money.of("1234.00", "EUR")


def test_new_conversion_helpers():
    assert TABLE.is_stale("USD", "EUR")
    assert not TABLE.is_stale("USD", "JPY")
    product = Product(sku="WIDGET", name="Widget", unit_price=Decimal("10.00"), currency="USD")
    assert TABLE.convert_product(product, "EUR") == Money.of("9.20", "EUR")
    note = TABLE.rate_note("USD", "EUR", Decimal("0.92"), AS_OF, Decimal("10.00"))
    assert note == "10.00 USD at 0.92 USD/EUR (published 2026-08-01)"
