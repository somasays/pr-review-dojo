"""Date range utilities.

Partition keys in the data lake are `dt` strings formatted YYYY-MM-DD. All
timestamps in the application are timezone-aware UTC.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

DT_FORMAT = "%Y-%m-%d"


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
    def last_n_days(cls, n: int, today: date | None = None) -> DateRange:
        """The n days ending yesterday. n=1 is just yesterday."""
        if n <= 0:
            raise ValueError("n must be positive")
        today = today or utcnow().date()
        end = today - timedelta(days=1)
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


def month_range(year: int, month: int) -> DateRange:
    start = date(year, month, 1)
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    return DateRange(start, nxt - timedelta(days=1))
