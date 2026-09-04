"""Adapts products and discount codes into domain pricing calls."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.db.models import Product
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


# Codes are static for now. A later change may move these to the database.
DISCOUNT_CODES: dict[str, Discount] = {
    "WELCOME10": Discount("WELCOME10", DiscountKind.PERCENT, Decimal("10")),
    "FLAT5": Discount("FLAT5", DiscountKind.FIXED, Decimal("5")),
    "BULK15": Discount(
        "BULK15", DiscountKind.THRESHOLD, Decimal("15"), min_subtotal=Money.of("200")
    ),
}


class PricingService:
    def resolve_discounts(self, codes: list[str]) -> list[Discount]:
        out = []
        for code in codes:
            normalized = code.strip().upper()
            if normalized not in DISCOUNT_CODES:
                raise UnknownDiscountCode(normalized)
            out.append(DISCOUNT_CODES[normalized])
        return out

    def build_lines(self, items: list[ItemRequest], products: dict[str, Product]) -> list[Line]:
        missing = [i.sku for i in items if i.sku not in products]
        if missing:
            raise UnknownSku(missing)
        lines = []
        for item in items:
            product = products[item.sku]
            if product.stock < item.quantity:
                raise InsufficientStock(item.sku, item.quantity, product.stock)
            lines.append(
                Line(
                    sku=item.sku,
                    unit_price=Money(product.unit_price, product.currency),
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
    ) -> Quote:
        lines = self.build_lines(items, products)
        discounts = self.resolve_discounts(codes)
        return quote(lines, discounts, region)
