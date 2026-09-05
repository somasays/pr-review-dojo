"""Adapts products and discount codes into domain pricing calls."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from app.db.models import Product
from app.domain.exchange import ExchangeRate, RateTable
from app.domain.money import Money
from app.domain.pricing import Discount, DiscountKind, Line, Quote, quote


@dataclass(frozen=True)
class ItemRequest:
    sku: str
    quantity: int


class UnknownDiscountCode(Exception):
    pass


class UnknownSku(Exception):
    def __init__(self, skus: list[str]) -> None:
        super().__init__(f"unknown skus: {', '.join(skus)}")
        self.skus = skus


class InsufficientStock(Exception):
    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(f"{sku}: requested {requested}, available {available}")
        self.sku = sku


# Fixed amounts and thresholds in the code table below are quoted in this currency.
DISCOUNT_CURRENCY = "USD"

# Published by finance every morning. A later change will read these from the
# database so we stop shipping a rate in the image.
DEFAULT_RATES = RateTable(
    (
        ExchangeRate("USD", "EUR", Decimal("0.92"), date(2026, 8, 1)),
        ExchangeRate("USD", "GBP", Decimal("0.79"), date(2026, 8, 1)),
    )
)

# Codes are static for now. A later change may move these to the database.
DISCOUNT_CODES: dict[str, Discount] = {
    "WELCOME10": Discount("WELCOME10", DiscountKind.PERCENT, Decimal("10")),
    "FLAT5": Discount("FLAT5", DiscountKind.FIXED, Decimal("5")),
    "BULK15": Discount(
        "BULK15", DiscountKind.THRESHOLD, Decimal("15"), min_subtotal=Money.of("200")
    ),
}


class PricingService:
    """Prices a cart in one currency, whatever currencies the catalog uses."""

    def __init__(self, rates: RateTable | None = None) -> None:
        self.rates = DEFAULT_RATES if rates is None else rates

    def resolve_discounts(
        self, codes: list[str], currency: str = DISCOUNT_CURRENCY
    ) -> list[Discount]:
        """Look up codes and restate their thresholds in the quote currency."""
        out = []
        for code in codes:
            normalized = code.strip().upper()
            if normalized not in DISCOUNT_CODES:
                raise UnknownDiscountCode(normalized)
            discount = DISCOUNT_CODES[normalized]
            if discount.min_subtotal is not None and discount.min_subtotal.currency != currency:
                discount = replace(
                    discount,
                    min_subtotal=self.rates.convert(discount.min_subtotal, currency),
                )
            out.append(discount)
        return out

    def build_lines(
        self,
        items: list[ItemRequest],
        products: dict[str, Product],
        currency: str = DISCOUNT_CURRENCY,
    ) -> list[Line]:
        """Build lines priced in `currency`, converting catalog prices as needed."""
        missing = [i.sku for i in items if i.sku not in products]
        if missing:
            raise UnknownSku(missing)
        lines = []
        for item in items:
            product = products[item.sku]
            if product.stock < item.quantity:
                raise InsufficientStock(item.sku, item.quantity, product.stock)
            unit_price = self.rates.convert(Money(product.unit_price, product.currency), currency)
            lines.append(
                Line(
                    sku=item.sku,
                    unit_price=unit_price,
                    quantity=item.quantity,
                )
            )
        return lines

    def quote(
        self,
        items: list[ItemRequest],
        products: dict[str, Product],
        codes: list[str],
        region: str,
        currency: str = DISCOUNT_CURRENCY,
    ) -> Quote:
        lines = self.build_lines(items, products, currency)
        discounts = self.resolve_discounts(codes, currency)
        return quote(lines, discounts, region)

    def refund_by_line(self, lines: list[Line], refund: Money) -> list[Money]:
        """Split a refund across lines in proportion to their subtotal."""
        weights = [int(ln.subtotal.amount * 100) for ln in lines]
        cents = int(refund.amount * 100)
        shares = [cents * w // sum(weights) for w in weights]
        rem = cents - sum(shares)
        return [Money(Decimal(s + (i < rem)) / 100, refund.currency) for i, s in enumerate(shares)]
