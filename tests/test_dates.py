from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from app.domain.dates import DateRange, ensure_utc, month_range, parse_dt, to_dt


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
