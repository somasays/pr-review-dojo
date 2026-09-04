"""Date range utilities.

Partition keys in the data lake are `dt` strings formatted YYYY-MM-DD. All
timestamps in the application are timezone-aware UTC.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple

DT_FORMAT = "%Y-%m-%d"
DAYS_PER_WEEK = 7
WEEKEND_DAYS = frozenset({5, 6})
FISCAL_YEAR_START_MONTH = 2


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def to_dt(day: date) -> str:
    """Format a date as a partition key."""
    return day.strftime(DT_FORMAT)


def parse_dt(value: str) -> date:
    """Parse a partition key. Raises ValueError on bad input."""
    return datetime.strptime(value, DT_FORMAT).date()


def ensure_utc(ts: datetime) -> datetime:
    """Reject naive datetimes and normalize aware ones to UTC."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime not allowed, attach a timezone")
    return ts.astimezone(UTC)


def partition_for(ts: datetime) -> str:
    """The dt partition key a timestamp belongs to."""
    return to_dt(ensure_utc(ts).date())


@dataclass(frozen=True, slots=True)
class DateRange:
    """Inclusive start, inclusive end, in whole days."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"end {self.end} before start {self.start}")

    @classmethod
    def single(cls, day: date) -> DateRange:
        return cls(day, day)

    @classmethod
    def last_n_days(
        cls, n: int, today: date | None = None, *, include_today: bool = False
    ) -> DateRange:
        """The n days ending yesterday, or ending today when include_today is set."""
        if n <= 0:
            raise ValueError("n must be positive")
        today = today or utcnow().date()
        end = today if include_today else today - timedelta(days=1)
        return cls(end - timedelta(days=n - 1), end)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def __iter__(self) -> Iterator[date]:
        cur = self.start
        while cur <= self.end:
            yield cur
            cur += timedelta(days=1)

    def partition_keys(self) -> list[str]:
        return [to_dt(d) for d in self]

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def overlaps(self, other: DateRange) -> bool:
        return self.start <= other.end and other.start <= self.end

    def split(self, chunk_days: int) -> list[DateRange]:
        """Split into consecutive ranges of at most chunk_days each."""
        if chunk_days <= 0:
            raise ValueError("chunk_days must be positive")
        out: list[DateRange] = []
        cur = self.start
        while cur <= self.end:
            chunk_end = min(cur + timedelta(days=chunk_days - 1), self.end)
            out.append(DateRange(cur, chunk_end))
            cur = chunk_end + timedelta(days=1)
        return out

    def split_weekly(self) -> list[DateRange]:
        """Split into consecutive weeks. The last chunk may be shorter."""
        return self.split(DAYS_PER_WEEK)


def month_range(year: int, month: int) -> DateRange:
    start = date(year, month, 1)
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return DateRange(start, nxt - timedelta(days=1))


def _month_start(year: int, month_index: int) -> date:
    """Month index 0 is January of `year`, and it may run past December."""
    return date(year + month_index // 12, month_index % 12 + 1, 1)


class FiscalQuarter(NamedTuple):
    """A fiscal year and quarter (1 to 4), still unpackable like the plain tuple it replaces."""

    year: int
    quarter: int


def fiscal_quarter(day: date) -> FiscalQuarter:
    """The fiscal year and quarter (1 to 4) that contain `day`."""
    year = day.year if day.month >= FISCAL_YEAR_START_MONTH else day.year - 1
    months_in = (day.month - FISCAL_YEAR_START_MONTH) % 12
    return FiscalQuarter(year, months_in // 3 + 1)


def quarter_range(fiscal_year: int, quarter: int) -> DateRange:
    """The inclusive range of a fiscal quarter."""
    if not 1 <= quarter <= 4:
        raise ValueError("quarter must be between 1 and 4")
    first = FISCAL_YEAR_START_MONTH - 1 + (quarter - 1) * 3
    return DateRange(
        _month_start(fiscal_year, first),
        _month_start(fiscal_year, first + 3) - timedelta(days=1),
    )


def current_quarter_range(today: date | None = None) -> DateRange:
    """The fiscal quarter that contains today."""
    today = today or utcnow().date()
    return quarter_range(*fiscal_quarter(today))


def is_business_day(day: date) -> bool:
    """True for Monday through Friday."""
    return day.weekday() not in WEEKEND_DAYS


def business_days(days: DateRange) -> int:
    """Number of business days in the range."""
    return sum(1 for day in days if is_business_day(day))


def next_business_day(day: date) -> date:
    """The next business day on or after `day`."""
    nxt = day + timedelta(days=1)
    while not is_business_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def parse_window(value: str) -> DateRange:
    """Parse `start:end` partition keys, or a single key for a one day window."""
    start_text, _, end_text = value.partition(":")
    start = parse_dt(start_text)
    return DateRange(start, parse_dt(end_text) if end_text else start)


def merge_ranges(ranges: Iterable[DateRange]) -> list[DateRange]:
    """Merge overlapping or adjacent ranges into the fewest ranges that cover them."""
    merged: list[DateRange] = []
    for rng in sorted(ranges, key=lambda r: r.start):
        if merged and rng.start <= merged[-1].end + timedelta(days=1):
            last = merged[-1]
            merged[-1] = DateRange(last.start, max(last.end, rng.end))
        else:
            merged.append(rng)
    return merged


@dataclass(frozen=True, slots=True)
class Coverage:
    """The merged ranges of a report request, with requested and covered day counts."""

    ranges: tuple[DateRange, ...]
    requested_days: int
    covered_days: int

    @property
    def duplicate_days(self) -> int:
        """Days that more than one requested range asked for."""
        return self.requested_days - self.covered_days

    @property
    def span(self) -> DateRange | None:
        """The first covered day through the last covered day, or None when nothing is covered."""
        if not self.ranges:
            return None
        return DateRange(self.ranges[0].start, self.ranges[-1].end)


def coverage(ranges: Iterable[DateRange]) -> Coverage:
    """Merge `ranges` and report how many days were asked for and how many are covered."""
    ranges = list(ranges)
    requested = sum(r.days for r in ranges)
    merged = merge_ranges(ranges)
    return Coverage(tuple(merged), requested, sum(r.days for r in merged))
