import pytest

from app.services.pricing_service import (
    ItemRequest,
    PricingService,
    UnknownDiscountCode,
    UnknownSku,
)


def test_resolve_discounts_normalizes_case(db, seeded):
    svc = PricingService(db)
    assert [d.code for d in svc.resolve_discounts([" flat5 "])] == ["FLAT5"]
    with pytest.raises(UnknownDiscountCode):
        svc.resolve_discounts(["NOPE"])


def test_unknown_sku(db, seeded):
    svc = PricingService(db)
    with pytest.raises(UnknownSku) as info:
        svc.build_lines([ItemRequest("MISSING", 1)], seeded["products"])
    assert info.value.skus == ["MISSING"]


def test_quote_uses_customer_region(db, seeded):
    q = PricingService(db).quote([ItemRequest("WIDGET", 1)], seeded["products"], [], "US-OR")
    assert q.tax.is_zero()
    assert str(q.total) == "19.99 USD"
