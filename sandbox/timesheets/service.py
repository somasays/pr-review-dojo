"""Shift recording, submission, decisions, and corrections: business rules
layered on the repositories.

Each public method opens exactly one unit of work (see
sandbox/timesheets/db.py) and either fully succeeds or leaves no trace.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.timesheets.db import Shift, Timesheet, coerce_utc, unit_of_work
from sandbox.timesheets.domain.pay import (
    InvalidTransition,
    Rules,
    TimesheetStatus,
    local_day,
    local_midnight_after,
    night_minutes,
    pay_cents,
    shift_minutes,
    split_overtime,
    transition,
    weekly_overtime,
)
from sandbox.timesheets.repo import ShiftRepo, TimesheetRepo, WorkerRepo

DEFAULT_RULES = Rules(
    daily_overtime_after_minutes=8 * 60,
    weekly_overtime_after_minutes=40 * 60,
    overtime_multiplier=Decimal("1.5"),
    night_start_hour=22,
    night_end_hour=6,
    night_differential=Decimal("0.10"),
)


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class Overlap(Exception):
    pass


class InvalidShift(Exception):
    pass


def period_start_for(day: date) -> date:
    """The Monday starting the Monday-to-Sunday pay period containing `day`."""
    return day - timedelta(days=day.weekday())


def period_totals(shifts: Sequence[Shift], tz: str, rules: Rules) -> tuple[int, int, int]:
    """Aggregate a timesheet's shifts into (regular, overtime, night) minutes
    for the whole pay period: daily overtime is split first, per local
    calendar day, then the remaining regular minutes are split again against
    the weekly threshold. Night minutes are counted independently."""
    minutes_by_day: dict[date, int] = {}
    daily_regular = 0
    daily_overtime = 0
    total_night = 0
    for shift in sorted(shifts, key=lambda s: s.start_utc):
        start_utc = coerce_utc(shift.start_utc)
        end_utc = coerce_utc(shift.end_utc)
        day = local_day(start_utc, tz)
        so_far = minutes_by_day.get(day, 0)
        regular, overtime = split_overtime(so_far, shift.minutes, rules)
        minutes_by_day[day] = so_far + shift.minutes
        daily_regular += regular
        daily_overtime += overtime
        total_night += night_minutes(start_utc, end_utc, tz, rules)

    weekly_regular, weekly_extra_overtime = weekly_overtime(daily_regular, rules)
    return weekly_regular, daily_overtime + weekly_extra_overtime, total_night


def _new_shift(
    timesheet_id: int, start_utc: datetime, end_utc: datetime, minutes: int, note: str | None
) -> Shift:
    return Shift(
        timesheet_id=timesheet_id, start_utc=start_utc, end_utc=end_utc, minutes=minutes, note=note
    )


class ShiftService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def record(
        self, worker_email: str, start_utc: datetime, end_utc: datetime, note: str | None
    ) -> Shift:
        """Record one shift, adding it to the open timesheet for the shift's
        local day's pay period, creating that timesheet if none exists. A
        shift that crosses the worker's local midnight is split into two
        rows at that boundary, sharing a group_id, so each part belongs to
        its own local day for daily overtime. Rejects shifts that overlap
        another one already on record."""
        try:
            total_minutes = shift_minutes(start_utc, end_utc)
        except ValueError as exc:
            raise InvalidShift(str(exc)) from exc

        with unit_of_work(self.session_factory) as session:
            workers = WorkerRepo(session)
            timesheets = TimesheetRepo(session)
            shifts = ShiftRepo(session)

            worker = workers.by_email(worker_email)
            if worker is None or not worker.active:
                raise NotFound(f"worker {worker_email!r} not found or inactive")

            boundary = local_midnight_after(start_utc, worker.timezone)
            crosses_midnight = boundary < end_utc
            if shifts.overlapping(worker.id, start_utc, end_utc):
                raise Overlap("this shift overlaps a shift already on record")

            period_start = period_start_for(local_day(start_utc, worker.timezone))
            timesheet = timesheets.current_for(worker.id, period_start)
            if timesheet is None:
                timesheet = timesheets.add(
                    Timesheet(
                        worker_id=worker.id,
                        period_start=period_start,
                        version=1,
                        status=TimesheetStatus.OPEN.value,
                    )
                )
            elif timesheet.status != TimesheetStatus.OPEN.value:
                raise NotAllowed(
                    f"the timesheet for {period_start.isoformat()} is not open for edits"
                )

            first_end = boundary if crosses_midnight else end_utc
            first_minutes = (
                shift_minutes(start_utc, boundary) if crosses_midnight else total_minutes
            )
            first = shifts.add(_new_shift(timesheet.id, start_utc, first_end, first_minutes, note))

            if not crosses_midnight:
                return first

            second_minutes = total_minutes - first_minutes
            second_local_day = local_day(boundary, worker.timezone)
            second_period_start = period_start_for(second_local_day)
            if second_period_start == period_start:
                second_timesheet = timesheet
            else:
                second_timesheet = timesheets.open_for_period(worker.id, second_period_start)
                if second_timesheet is None:
                    second_timesheet = timesheets.add(
                        Timesheet(
                            worker_id=worker.id,
                            period_start=second_period_start,
                            version=1,
                            status=TimesheetStatus.OPEN.value,
                        )
                    )

            second = shifts.add(
                _new_shift(second_timesheet.id, boundary, end_utc, second_minutes, note)
            )
            first.group_id = first.id
            second.group_id = first.id
            return first


class TimesheetService:
    def __init__(
        self, session_factory: sessionmaker[Session], rules: Rules = DEFAULT_RULES
    ) -> None:
        self.session_factory = session_factory
        self.rules = rules

    def submit(self, worker_email: str, period_start: date) -> Timesheet:
        """Compute the period's regular, overtime, night, and total-pay
        totals from its shifts, then move the timesheet to submitted."""
        with unit_of_work(self.session_factory) as session:
            workers = WorkerRepo(session)
            timesheets = TimesheetRepo(session)

            worker = workers.by_email(worker_email)
            if worker is None:
                raise NotFound(f"worker {worker_email!r} not found")

            timesheet = timesheets.current_for(worker.id, period_start)
            if timesheet is None:
                raise NotFound(f"no timesheet for {worker_email!r} in {period_start.isoformat()}")

            shifts = ShiftRepo(session).for_timesheet(timesheet.id)
            if not shifts:
                raise NotAllowed("a timesheet with no shifts cannot be submitted")

            regular, overtime, night = period_totals(shifts, worker.timezone, self.rules)
            try:
                timesheet.status = transition(
                    TimesheetStatus(timesheet.status), TimesheetStatus.SUBMITTED
                ).value
            except InvalidTransition as exc:
                raise NotAllowed(str(exc)) from exc

            timesheet.total_pay = pay_cents(
                regular, overtime, night, worker.hourly_rate, self.rules
            )
            timesheet.submitted_at = datetime.now(UTC)
            return timesheet

    def decide(
        self, manager_email: str, timesheet_id: int, approve: bool, reason: str | None
    ) -> Timesheet:
        """Approve or reject a submitted timesheet. A manager may not
        decide a timesheet belonging to their own worker record; rejecting
        one requires a reason."""
        if not approve and not reason:
            raise NotAllowed("a reason is required to reject a timesheet")

        with unit_of_work(self.session_factory) as session:
            timesheets = TimesheetRepo(session)
            workers = WorkerRepo(session)

            timesheet = timesheets.get(timesheet_id)
            if timesheet is None:
                raise NotFound(f"timesheet {timesheet_id} not found")

            owner = workers.get(timesheet.worker_id)
            if owner is not None and owner.email == manager_email:
                raise NotAllowed("a manager may not decide their own timesheet")

            target = TimesheetStatus.APPROVED if approve else TimesheetStatus.REJECTED
            try:
                timesheet.status = transition(TimesheetStatus(timesheet.status), target).value
            except InvalidTransition as exc:
                raise NotAllowed(str(exc)) from exc

            timesheet.decided_at = datetime.now(UTC)
            timesheet.decided_by = manager_email
            return timesheet

    def correct(self, worker_email: str, timesheet_id: int) -> Timesheet:
        """Start a correction: a new, open version of an approved
        timesheet, cloned with its original shifts. Corrections after
        approval are always a new version, never an edit of the old one."""
        with unit_of_work(self.session_factory) as session:
            workers = WorkerRepo(session)
            timesheets = TimesheetRepo(session)
            shifts = ShiftRepo(session)

            worker = workers.by_email(worker_email)
            if worker is None:
                raise NotFound(f"worker {worker_email!r} not found")

            original = timesheets.get(timesheet_id)
            if original is None:
                raise NotFound(f"timesheet {timesheet_id} not found")
            if original.worker_id != worker.id:
                raise NotAllowed("a worker may only correct their own timesheet")
            if original.status != TimesheetStatus.APPROVED.value:
                raise NotAllowed("only an approved timesheet may be corrected")

            corrected = timesheets.add(
                Timesheet(
                    worker_id=original.worker_id,
                    period_start=original.period_start,
                    version=original.version + 1,
                    status=TimesheetStatus.OPEN.value,
                )
            )
            for shift in shifts.for_timesheet(original.id):
                shifts.add(
                    Shift(
                        timesheet_id=corrected.id,
                        start_utc=shift.start_utc,
                        end_utc=shift.end_utc,
                        minutes=shift.minutes,
                        note=shift.note,
                    )
                )
            return corrected
