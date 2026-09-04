"""Hidden tests for exercise 19."""

import ast
import inspect
import pathlib
from datetime import date
from decimal import Decimal

from app.domain.exchange import ExchangeRate, RateTable
from app.domain.money import Money, sum_money
from app.domain.pricing import Discount, DiscountKind, Line, line_shares, line_tax
from app.services.pricing_service import ItemRequest, PricingService

DOMAIN_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "domain"
FORBIDDEN_IMPORTS = ("app.db", "app.services", "app.api")


def test_to_cents_is_exact():
    assert Money.of("0.29").to_cents() == 29
    assert Money.of("4.35").to_cents() == 435
    assert Money.of("-0.29").to_cents() == -29


def test_allocate_by_sums_to_the_original():
    parts = Money.of("1.00").allocate_by([1, 1, 1])
    assert parts == [Money.of("0.34"), Money.of("0.33"), Money.of("0.33")]
    assert sum_money(parts) == Money.of("1.00")

    weighted = Money.of("10.00").allocate_by([3, 1])
    assert sum_money(weighted) == Money.of("10.00")


def test_threshold_without_a_minimum_applies():
    anytime = Discount("ANY15", DiscountKind.THRESHOLD, Decimal("15"))
    assert anytime.apply(Money.of("100.00")) == Money.of("15.00")

    gated = Discount(
        "BULK15", DiscountKind.THRESHOLD, Decimal("15"), min_subtotal=Money.of("200.00")
    )
    assert gated.apply(Money.of("199.99")).is_zero()
    assert gated.apply(Money.of("200.00")) == Money.of("30.00")


def test_line_tax_rounds_half_up_like_money():
    line = Line("A", Money.of("53.30"), 1)
    assert line_tax(line, Decimal("5")) == Money.of("2.67")
    assert line_tax(line, Decimal("5")) == Money.of("53.30").percent(Decimal("5"))


def test_line_shares_handles_a_free_order():
    assert line_shares([Line("SAMPLE", Money.zero(), 1)], Money.zero()) == [Money.zero()]


def test_fixed_code_is_converted_into_the_quote_currency(db, seeded):
    """FLAT5 is five USD, so a EUR quote must take the converted amount off."""
    svc = PricingService()
    products = seeded["products"]

    usd = svc.quote([ItemRequest("WIDGET", 1)], products, ["FLAT5"], "US-OR")
    assert usd.discount == Money.of("5.00")

    eur = svc.quote([ItemRequest("WIDGET", 1)], products, ["FLAT5"], "US-OR", currency="EUR")
    assert eur.subtotal == Money.of("18.39", "EUR")
    assert eur.discount == Money.of("4.60", "EUR")
    assert eur.total == Money.of("13.79", "EUR")


def test_domain_does_not_import_outward():
    """DS-06: app/domain must not depend on app.db, app.services, or app.api."""
    for path in DOMAIN_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                assert not name.startswith(FORBIDDEN_IMPORTS), f"{path.name} imports {name}"


def test_is_stale_takes_a_fixed_today():
    """DS-09 (clock inside pure logic) and TR-06 (the shipped test pinned the date)."""
    assert "today" in inspect.signature(RateTable.is_stale).parameters
    as_of = date(2026, 8, 1)
    table = RateTable((ExchangeRate("USD", "EUR", Decimal("0.92"), as_of),))
    assert table.is_stale("USD", "EUR", today=date(2026, 8, 3))
    assert not table.is_stale("USD", "EUR", today=date(2026, 8, 1))


def test_refund_by_line_reuses_allocate_by():
    """DS-08: reuse Money.allocate_by instead of re-implementing the split."""
    source = (
        pathlib.Path(__file__).resolve().parents[1] / "app/services/pricing_service.py"
    ).read_text()
    tree = ast.parse(source)
    calls = {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "allocate_by" in calls

    lines = [Line("A", Money.of("30.00"), 1), Line("B", Money.of("70.00"), 1)]
    parts = PricingService().refund_by_line(lines, Money.of("10.00"))
    assert parts == [Money.of("3.00"), Money.of("7.00")]
    assert sum_money(parts) == Money.of("10.00")


def test_rate_note_uses_the_domain_type():
    """Refactor (DS-13): prefer ExchangeRate over five loose primitives."""
    params = [
        p for name, p in inspect.signature(RateTable.rate_note).parameters.items() if name != "self"
    ]
    assert len(params) <= 3 or any(p.annotation is ExchangeRate for p in params)
