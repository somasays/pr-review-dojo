"""Adapts products and discount codes into domain pricing calls."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import DiscountCode, Product
from app.db.repositories import DiscountCodeRepository
from app.domain.money import Money
from app.domain.pricing import Discount, DiscountKind, Line, Quote, quote


@dataclass(frozen=True)
class ItemRequest:
    sku: str
    quantity: int


class UnknownDiscountCode(Exception):
    pass


class DiscountExhausted(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"{code} has reached its redemption limit")
        self.code = code


class UnknownSku(Exception):
    def __init__(self, skus: list[str]) -> None:
        super().__init__(f"unknown skus: {', '.join(skus)}")
        self.skus = skus


class InsufficientStock(Exception):
    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(f"{sku}: requested {requested}, available {available}")
        self.sku = sku


def to_discount(row: DiscountCode) -> Discount:
    """Turn a stored code into the pure domain rule."""
    min_subtotal = Money(row.min_subtotal) if row.min_subtotal is not None else None
    return Discount(
        code=row.code,
        kind=DiscountKind(row.kind),
        value=row.value,
        min_subtotal=min_subtotal,
    )


class PricingService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.discounts = DiscountCodeRepository(session)

    def resolve_discounts(self, codes: list[str]) -> list[Discount]:
        out = []
        for code in codes:
            normalized = code.strip().upper()
            row = self.discounts.by_code(normalized)
            if row is None or not row.active:
                raise UnknownDiscountCode(normalized)
            if row.max_redemptions is not None and row.times_redeemed >= row.max_redemptions:
                raise DiscountExhausted(normalized)
            out.append(to_discount(row))
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
