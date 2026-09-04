"""Hidden tests for exercise 07."""

import ast
import inspect
import textwrap
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.domain.dates import DateRange, coverage, fiscal_quarter, partition_for


def test_coverage_accepts_a_one_shot_iterable():
    ranges = [DateRange(date(2026, 8, 1), date(2026, 8, 3)), DateRange.single(date(2026, 8, 4))]
    cov = coverage(iter(ranges))
    assert cov.ranges == (DateRange(date(2026, 8, 1), date(2026, 8, 4)),)
    assert cov.requested_days == 4
    assert cov.covered_days == 4


def test_coverage_of_nothing_has_no_span():
    cov = coverage([])
    assert cov.ranges == ()
    assert cov.covered_days == 0
    assert cov.span is None


def test_include_today_keeps_the_window_n_days_long():
    r = DateRange.last_n_days(7, today=date(2026, 8, 10), include_today=True)
    assert r.days == 7
    assert r == DateRange(date(2026, 8, 4), date(2026, 8, 10))
    one = DateRange.last_n_days(1, today=date(2026, 8, 10), include_today=True)
    assert one == DateRange.single(date(2026, 8, 10))


def test_partition_for_buckets_by_utc_day():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert partition_for(datetime(2026, 8, 2, 1, 0, tzinfo=ist)) == "2026-08-01"
    assert partition_for(datetime(2026, 8, 2, 23, 30, tzinfo=UTC)) == "2026-08-02"
    with pytest.raises(ValueError):
        partition_for(datetime(2026, 8, 2, 1, 0))


def test_split_weekly_delegates_to_split():
    """DS-08: split_weekly must reuse DateRange.split instead of hand-rolling the loop."""
    source = textwrap.dedent(inspect.getsource(DateRange.split_weekly))
    tree = ast.parse(source)
    calls_split = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "split"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        for node in ast.walk(tree)
    )
    has_while_loop = any(isinstance(node, ast.While) for node in ast.walk(tree))
    assert calls_split, "split_weekly should call self.split(...)"
    assert not has_while_loop, "split_weekly should not hand-roll the chunking loop"

    # And it must still be correct, since delegating also fixes the off-by-one.
    fortnight = DateRange(date(2026, 8, 1), date(2026, 8, 14))
    assert [r.days for r in fortnight.split_weekly()] == [7, 7]
    assert [r.days for r in DateRange(date(2026, 8, 1), date(2026, 8, 10)).split_weekly()] == [7, 3]


def test_fiscal_quarter_returns_a_named_type():
    """DS-13 (refactor): the (year, quarter) pair should be a small named type, not a bare tuple."""
    result = fiscal_quarter(date(2026, 2, 1))
    assert result == (2026, 1)
    assert hasattr(result, "year")
    assert hasattr(result, "quarter")
    assert result.year == 2026
    assert result.quarter == 1


def test_render_weekly_orders_is_a_pure_function_of_plain_values():
    """DS-21: the weekly breakdown must be computable without a DB session."""
    from app.api.routers.reports import render_weekly_orders

    span = DateRange(date(2026, 8, 1), date(2026, 8, 14))
    order_days = [date(2026, 8, 2), date(2026, 8, 2), date(2026, 8, 10)]
    weeks = render_weekly_orders(span, order_days)
    assert [w.orders for w in weeks] == [2, 1]
