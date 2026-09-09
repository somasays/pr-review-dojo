"""Tests for ShiftService and TimesheetService."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.timesheets.domain.pay import TimesheetStatus
from sandbox.timesheets.repo import ShiftRepo, TimesheetRepo
from sandbox.timesheets.service import (
    NotAllowed,
    NotFound,
    Overlap,
    ShiftService,
    TimesheetService,
    period_start_for,
)
from sandbox.timesheets.tests.conftest import (
    MANAGER_EMAIL,
    NY_WORKER_EMAIL,
    SELF_MANAGER_EMAIL,
    WORKER_EMAIL,
)

PERIOD_START = date(2026, 1, 5)


def _la_utc(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    local = datetime(y, m, d, h, mi, tzinfo=ZoneInfo("America/Los_Angeles"))
    return local.astimezone(UTC)


def test_record_creates_the_open_timesheet_for_the_shifts_local_period(
    session_factory: sessionmaker[Session], seeded, db: Session
) -> None:
    service = ShiftService(session_factory)
    shift = service.record(WORKER_EMAIL, _la_utc(2026, 1, 5, 8), _la_utc(2026, 1, 5, 12), "am")

    timesheet = TimesheetRepo(db).get(shift.timesheet_id)
    assert timesheet is not None
    assert timesheet.status == TimesheetStatus.OPEN.value
    assert timesheet.period_start == period_start_for(date(2026, 1, 5))
    assert shift.minutes == 240


def test_record_rejects_overlap_and_shifts_on_a_non_open_timesheet(
    session_factory: sessionmaker[Session], seeded, db: Session
) -> None:
    service = ShiftService(session_factory)
    service.record(WORKER_EMAIL, _la_utc(2026, 1, 5, 8), _la_utc(2026, 1, 5, 12), None)

    with pytest.raises(Overlap):
        service.record(WORKER_EMAIL, _la_utc(2026, 1, 5, 10), _la_utc(2026, 1, 5, 14), None)

    timesheet = TimesheetRepo(db).current_for(seeded["priya"].id, PERIOD_START)
    assert timesheet is not None
    timesheet.status = TimesheetStatus.SUBMITTED.value
    db.commit()

    with pytest.raises(NotAllowed):
        service.record(WORKER_EMAIL, _la_utc(2026, 1, 5, 14), _la_utc(2026, 1, 5, 16), None)


def test_submit_computes_daily_weekly_overtime_and_night_pay(
    session_factory: sessionmaker[Session], seeded
) -> None:
    shift_service = ShiftService(session_factory)
    shift_service.record(WORKER_EMAIL, _la_utc(2026, 1, 5, 8), _la_utc(2026, 1, 5, 18), None)
    shift_service.record(WORKER_EMAIL, _la_utc(2026, 1, 6, 20), _la_utc(2026, 1, 6, 23), None)
    shift_service.record(WORKER_EMAIL, _la_utc(2026, 1, 7, 0), _la_utc(2026, 1, 7, 6), None)

    timesheet = TimesheetService(session_factory).submit(WORKER_EMAIL, PERIOD_START)

    assert timesheet.status == TimesheetStatus.SUBMITTED.value
    assert timesheet.total_pay == Decimal("465.75")
    assert timesheet.submitted_at is not None


def test_submit_rejects_a_timesheet_with_no_shifts(
    session_factory: sessionmaker[Session], seeded
) -> None:
    with pytest.raises(NotFound):
        TimesheetService(session_factory).submit(WORKER_EMAIL, PERIOD_START)


def test_decide_approves_and_transitions_status(
    session_factory: sessionmaker[Session], seeded
) -> None:
    shift_service = ShiftService(session_factory)
    shift_service.record(NY_WORKER_EMAIL, _la_utc(2026, 1, 5, 8), _la_utc(2026, 1, 5, 12), None)
    timesheet = TimesheetService(session_factory).submit(NY_WORKER_EMAIL, PERIOD_START)

    decided = TimesheetService(session_factory).decide(MANAGER_EMAIL, timesheet.id, True, None)
    assert decided.status == TimesheetStatus.APPROVED.value
    assert decided.decided_by == MANAGER_EMAIL


def test_decide_rejects_self_approval_and_requires_a_reason_to_reject(
    session_factory: sessionmaker[Session], seeded
) -> None:
    shift_service = ShiftService(session_factory)
    shift_service.record(SELF_MANAGER_EMAIL, _la_utc(2026, 1, 5, 8), _la_utc(2026, 1, 5, 12), None)
    timesheet = TimesheetService(session_factory).submit(SELF_MANAGER_EMAIL, PERIOD_START)

    with pytest.raises(NotAllowed, match="own timesheet"):
        TimesheetService(session_factory).decide(SELF_MANAGER_EMAIL, timesheet.id, True, None)

    with pytest.raises(NotAllowed, match="reason"):
        TimesheetService(session_factory).decide(MANAGER_EMAIL, timesheet.id, False, None)


def test_correct_creates_a_new_open_version_with_cloned_shifts(
    session_factory: sessionmaker[Session], seeded, db: Session
) -> None:
    shift_service = ShiftService(session_factory)
    shift_service.record(NY_WORKER_EMAIL, _la_utc(2026, 1, 5, 8), _la_utc(2026, 1, 5, 12), None)
    timesheet_service = TimesheetService(session_factory)
    original = timesheet_service.submit(NY_WORKER_EMAIL, PERIOD_START)
    approved = timesheet_service.decide(MANAGER_EMAIL, original.id, True, None)

    corrected = timesheet_service.correct(NY_WORKER_EMAIL, approved.id)

    assert corrected.version == approved.version + 1
    assert corrected.status == TimesheetStatus.OPEN.value
    assert corrected.total_pay is None
    cloned_shifts = ShiftRepo(db).for_timesheet(corrected.id)
    assert len(cloned_shifts) == 1

    # The new version is open, not approved, so it cannot be corrected again.
    with pytest.raises(NotAllowed):
        timesheet_service.correct(NY_WORKER_EMAIL, corrected.id)
