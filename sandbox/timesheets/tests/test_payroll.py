"""Tests for the payroll CSV export job."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session, sessionmaker

from sandbox.timesheets.payroll import export_period
from sandbox.timesheets.repo import TimesheetRepo
from sandbox.timesheets.service import ShiftService, TimesheetService
from sandbox.timesheets.tests.conftest import MANAGER_EMAIL, NY_WORKER_EMAIL, WORKER_EMAIL

PERIOD_START = date(2026, 1, 5)


def _la_utc(y: int, m: int, d: int, h: int) -> datetime:
    local = datetime(y, m, d, h, tzinfo=ZoneInfo("America/Los_Angeles"))
    return local.astimezone(UTC)


def _approve_a_timesheet(session_factory: sessionmaker[Session], worker_email: str) -> int:
    ShiftService(session_factory).record(
        worker_email, _la_utc(2026, 1, 5, 8), _la_utc(2026, 1, 5, 12), None
    )
    timesheet_service = TimesheetService(session_factory)
    submitted = timesheet_service.submit(worker_email, PERIOD_START)
    approved = timesheet_service.decide(MANAGER_EMAIL, submitted.id, True, None)
    return approved.id


def test_export_period_writes_only_the_latest_approved_version(
    session_factory: sessionmaker[Session], seeded, db: Session, tmp_path: Path
) -> None:
    approved_id = _approve_a_timesheet(session_factory, WORKER_EMAIL)

    # Start a correction: a second, still-open version now exists for the
    # same worker and period. The export must still pick the approved row.
    TimesheetService(session_factory).correct(WORKER_EMAIL, approved_id)
    assert len(TimesheetRepo(db).for_period(seeded["priya"].id, PERIOD_START)) == 2

    out = tmp_path / "payroll.csv"
    count = export_period(session_factory, PERIOD_START, out)
    assert count == 1

    with out.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["worker_email"] == WORKER_EMAIL
    assert rows[0]["regular_minutes"] == "240"
    assert rows[0]["overtime_minutes"] == "0"


def test_export_period_is_idempotent(
    session_factory: sessionmaker[Session], seeded, tmp_path: Path
) -> None:
    _approve_a_timesheet(session_factory, NY_WORKER_EMAIL)
    out = tmp_path / "payroll.csv"

    first = export_period(session_factory, PERIOD_START, out)
    second = export_period(session_factory, PERIOD_START, out)

    assert first == second == 1
    with out.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
