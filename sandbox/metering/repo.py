"""Repositories: the only place that builds queries.

Convention (see sandbox/metering/README.md): bound parameters only, flush
but never commit. The API dependency owns the transaction through
db.session_scope().
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from sandbox.metering.db import Account, Bill, Meter, Reading


class AccountRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_email(self, email: str) -> Account | None:
        stmt = select(Account).where(Account.email == email)
        return self.session.scalars(stmt).first()

    def get(self, account_id: int) -> Account | None:
        return self.session.get(Account, account_id)


class MeterRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_serial(self, serial: str) -> Meter | None:
        stmt = select(Meter).where(Meter.serial == serial)
        return self.session.scalars(stmt).first()

    def get(self, meter_id: int) -> Meter | None:
        return self.session.get(Meter, meter_id)

    def for_account(self, account_id: int) -> Sequence[Meter]:
        stmt = select(Meter).where(Meter.account_id == account_id)
        return self.session.scalars(stmt).all()

    def all(self) -> Sequence[Meter]:
        return self.session.scalars(select(Meter)).all()


class ReadingRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _superseded_ids(self) -> Select[tuple[int]]:
        return select(Reading.supersedes_id).where(Reading.supersedes_id.is_not(None))

    def add(self, reading: Reading) -> Reading:
        self.session.add(reading)
        self.session.flush()
        return reading

    def get(self, reading_id: int) -> Reading | None:
        return self.session.get(Reading, reading_id)

    def _latest_non_superseded(
        self, meter_id: int, *, before: datetime | None = None, inclusive: bool = False
    ) -> Reading | None:
        stmt = select(Reading).where(
            Reading.meter_id == meter_id, Reading.id.not_in(self._superseded_ids())
        )
        if before is not None:
            stmt = stmt.where(
                Reading.taken_at <= before if inclusive else Reading.taken_at < before
            )
        stmt = stmt.order_by(Reading.taken_at.desc(), Reading.id.desc()).limit(1)
        return self.session.scalars(stmt).first()

    def latest(self, meter_id: int) -> Reading | None:
        """The latest non-superseded reading for meter_id, of any source."""
        return self._latest_non_superseded(meter_id)

    def latest_before(self, meter_id: int, at: datetime) -> Reading | None:
        """The latest non-superseded reading for meter_id taken strictly
        before `at`."""
        return self._latest_non_superseded(meter_id, before=at)

    def latest_at_or_before(self, meter_id: int, at: datetime) -> Reading | None:
        """The latest non-superseded reading for meter_id taken at or
        before `at`."""
        return self._latest_non_superseded(meter_id, before=at, inclusive=True)

    def last_two_actual(self, meter_id: int) -> Sequence[Reading]:
        """The two most recent non-superseded readings with source
        "actual", newest first. Used by the estimate job, which never
        extrapolates from its own estimates."""
        stmt = (
            select(Reading)
            .where(
                Reading.meter_id == meter_id,
                Reading.source == "actual",
                Reading.id.not_in(self._superseded_ids()),
            )
            .order_by(Reading.taken_at.desc(), Reading.id.desc())
            .limit(2)
        )
        return self.session.scalars(stmt).all()

    def is_superseded(self, reading_id: int) -> bool:
        """Whether some other reading's `supersedes_id` already points at
        reading_id."""
        stmt = select(Reading.id).where(Reading.supersedes_id == reading_id)
        return self.session.scalars(stmt).first() is not None

    def supersede(self, reading_id: int, replacement: Reading) -> Reading:
        """Append `replacement` with `supersedes_id` set to reading_id. The
        superseded row itself is never updated or deleted."""
        replacement.supersedes_id = reading_id
        self.session.add(replacement)
        self.session.flush()
        return replacement


class BillRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, bill: Bill) -> Bill:
        self.session.add(bill)
        self.session.flush()
        return bill

    def for_period(self, account_id: int, start: date, end: date) -> Bill | None:
        stmt = select(Bill).where(
            Bill.account_id == account_id,
            Bill.period_start == start,
            Bill.period_end == end,
        )
        return self.session.scalars(stmt).first()

    def for_account(self, account_id: int) -> Sequence[Bill]:
        stmt = select(Bill).where(Bill.account_id == account_id).order_by(Bill.period_start)
        return self.session.scalars(stmt).all()

    def affected_by(self, account_id: int, taken_at: datetime) -> Sequence[Bill]:
        """Original bills for account_id whose half-open period
        [period_start, period_end) covers taken_at's date."""
        at_date = taken_at.date()
        stmt = select(Bill).where(
            Bill.account_id == account_id,
            Bill.period_start <= at_date,
            Bill.period_end > at_date,
            Bill.adjusts_bill_id.is_(None),
        )
        return self.session.scalars(stmt).all()

    def for_correction(self, adjusts_bill_id: int, correction_reading_id: int) -> Bill | None:
        """The adjustment already issued for this (bill, correction) pair, if any."""
        stmt = select(Bill).where(
            Bill.adjusts_bill_id == adjusts_bill_id,
            Bill.correction_reading_id == correction_reading_id,
        )
        return self.session.scalars(stmt).first()
