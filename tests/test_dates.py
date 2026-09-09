from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.domain.dates import (
    DateRange,
    business_days,
    coverage,
    current_quarter_range,
    ensure_utc,
    fiscal_quarter,
    month_range,
    next_business_day,
    parse_dt,
    partition_for,
    quarter_range,
    to_dt,
)


def test_dt_roundtrip():
    assert parse_dt(to_dt(date(2026, 2, 28))) == date(2026, 2, 28)
    with pytest.raises(ValueError):
        parse_dt("2026/02/28")


def test_range_days_and_iteration():
    r = DateRange(date(2026, 1, 30), date(2026, 2, 2))
    assert r.days == 4
    assert r.partition_keys() == ["2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"]
    with pytest.raises(ValueError):
        DateRange(date(2026, 1, 2), date(2026, 1, 1))


def test_last_n_days_ends_yesterday():
    r = DateRange.last_n_days(3, today=date(2026, 3, 10))
    assert r == DateRange(date(2026, 3, 7), date(2026, 3, 9))
    assert DateRange.last_n_days(1, today=date(2026, 3, 10)).days == 1


def test_split_and_overlap():
    r = DateRange(date(2026, 1, 1), date(2026, 1, 10))
    chunks = r.split(4)
    assert [c.days for c in chunks] == [4, 4, 2]
    assert chunks[0].overlaps(DateRange.single(date(2026, 1, 4)))
    assert not chunks[0].overlaps(chunks[1])


def test_month_range_december():
    assert month_range(2026, 12).end == date(2026, 12, 31)
    assert month_range(2024, 2).days == 29


def test_ensure_utc():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))
    est = datetime(2026, 1, 1, 12, tzinfo=timezone(timedelta(hours=-5)))
    assert ensure_utc(est) == datetime(2026, 1, 1, 17, tzinfo=UTC)


def test_fiscal_quarter_and_range():
    assert fiscal_quarter(date(2026, 2, 1)) == (2026, 1)
    assert fiscal_quarter(date(2026, 1, 15)) == (2025, 4)
    assert quarter_range(2026, 1) == DateRange(date(2026, 2, 1), date(2026, 4, 30))
    assert quarter_range(2025, 4) == DateRange(date(2025, 11, 1), date(2026, 1, 31))
    with pytest.raises(ValueError):
        quarter_range(2026, 5)


def test_current_quarter_range_uses_today():
    q3 = DateRange(date(2026, 8, 1), date(2026, 10, 31))
    assert current_quarter_range(date(2026, 9, 4)) == q3


def test_business_days():
    # Monday 2026-08-03 through Sunday 2026-08-09.
    week = DateRange(date(2026, 8, 3), date(2026, 8, 9))
    assert business_days(week) == 5
    assert next_business_day(date(2026, 8, 7)) == date(2026, 8, 10)
    assert next_business_day(date(2026, 8, 3)) == date(2026, 8, 4)


def test_coverage_counts_duplicate_days():
    cov = coverage(
        [
            DateRange(date(2026, 8, 1), date(2026, 8, 3)),
            DateRange(date(2026, 8, 3), date(2026, 8, 4)),
        ]
    )
    assert cov.requested_days == 5
    assert cov.covered_days == 4
    assert cov.duplicate_days == 1
    assert cov.span == DateRange(date(2026, 8, 1), date(2026, 8, 4))


def test_partition_for():
    assert partition_for(datetime(2026, 8, 2, 23, 30, tzinfo=UTC)) == "2026-08-02"
