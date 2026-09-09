"""Repositories: the only place that builds queries.

Convention (see sandbox/timesheets/README.md): repositories flush but never
commit. The service layer owns the transaction through db.unit_of_work().
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sandbox.timesheets.db import Shift, Timesheet, Worker
from sandbox.timesheets.domain.pay import TimesheetStatus

_OPEN_OR_SUBMITTED = (TimesheetStatus.OPEN.value, TimesheetStatus.SUBMITTED.value)


class WorkerRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def by_email(self, email: str) -> Worker | None:
        stmt = select(Worker).where(Worker.email == email)
        return self.session.scalars(stmt).first()

    def get(self, worker_id: int) -> Worker | None:
        return self.session.get(Worker, worker_id)


class TimesheetRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, timesheet: Timesheet) -> Timesheet:
        self.session.add(timesheet)
        self.session.flush()
        return timesheet

    def get(self, timesheet_id: int) -> Timesheet | None:
        return self.session.get(Timesheet, timesheet_id)

    def current_for(self, worker_id: int, period_start: date) -> Timesheet | None:
        """The highest-versioned timesheet for this worker and period."""
        stmt = (
            select(Timesheet)
            .where(Timesheet.worker_id == worker_id, Timesheet.period_start == period_start)
            .order_by(Timesheet.version.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def open_for_period(self, worker_id: int, period_start: date) -> Timesheet | None:
        """The open timesheet for this worker and period, or None if the
        period does not have one yet (or its only timesheet is no longer
        open)."""
        stmt = (
            select(Timesheet)
            .where(
                Timesheet.worker_id == worker_id,
                Timesheet.period_start == period_start,
                Timesheet.status == TimesheetStatus.OPEN.value,
            )
            .order_by(Timesheet.version.desc())
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def for_period(self, worker_id: int, period_start: date) -> Sequence[Timesheet]:
        """Every version for this worker and period, oldest first."""
        stmt = (
            select(Timesheet)
            .where(Timesheet.worker_id == worker_id, Timesheet.period_start == period_start)
            .order_by(Timesheet.version)
        )
        return self.session.scalars(stmt).all()

    def pending(self) -> Sequence[Timesheet]:
        """Timesheets awaiting a decision, oldest submission first."""
        stmt = (
            select(Timesheet)
            .where(Timesheet.status == TimesheetStatus.SUBMITTED.value)
            .order_by(Timesheet.submitted_at)
        )
        return self.session.scalars(stmt).all()


class ShiftRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, shift: Shift) -> Shift:
        self.session.add(shift)
        self.session.flush()
        return shift

    def for_timesheet(self, timesheet_id: int) -> Sequence[Shift]:
        stmt = select(Shift).where(Shift.timesheet_id == timesheet_id).order_by(Shift.start_utc)
        return self.session.scalars(stmt).all()

    def for_group(self, group_id: int) -> Sequence[Shift]:
        """Every part of a shift split at local midnight, oldest first."""
        stmt = select(Shift).where(Shift.group_id == group_id).order_by(Shift.start_utc)
        return self.session.scalars(stmt).all()

    def overlapping(
        self, worker_id: int, start_utc: datetime, end_utc: datetime
    ) -> Sequence[Shift]:
        """Shifts that overlap `[start_utc, end_utc)`, across every open or
        submitted timesheet belonging to this worker."""
        stmt = (
            select(Shift)
            .join(Timesheet, Shift.timesheet_id == Timesheet.id)
            .where(
                Timesheet.worker_id == worker_id,
                Timesheet.status.in_(_OPEN_OR_SUBMITTED),
                Shift.start_utc < end_utc,
                Shift.end_utc > start_utc,
            )
        )
        return self.session.scalars(stmt).all()
