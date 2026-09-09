"""Tests for the repository query layer."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from sandbox.timesheets.db import Shift, Timesheet, Worker
from sandbox.timesheets.repo import ShiftRepo, TimesheetRepo, WorkerRepo


def test_worker_repo_by_email(db: Session, seeded: dict[str, Worker]) -> None:
    found = WorkerRepo(db).by_email("priya@example.com")
    assert found is not None
    assert found.id == seeded["priya"].id
    assert WorkerRepo(db).by_email("nobody@example.com") is None


def test_timesheet_repo_current_for_returns_the_highest_version(
    db: Session, seeded: dict[str, Worker]
) -> None:
    worker_id = seeded["priya"].id
    period = date(2026, 1, 5)
    repo = TimesheetRepo(db)
    repo.add(Timesheet(worker_id=worker_id, period_start=period, version=1, status="approved"))
    v2 = repo.add(Timesheet(worker_id=worker_id, period_start=period, version=2, status="open"))

    current = repo.current_for(worker_id, period)
    assert current is not None
    assert current.id == v2.id
    assert len(repo.for_period(worker_id, period)) == 2


def test_timesheet_repo_open_for_period(db: Session, seeded: dict[str, Worker]) -> None:
    worker_id = seeded["priya"].id
    period = date(2026, 1, 5)
    repo = TimesheetRepo(db)

    assert repo.open_for_period(worker_id, period) is None

    opened = repo.add(Timesheet(worker_id=worker_id, period_start=period, version=1, status="open"))
    found = repo.open_for_period(worker_id, period)
    assert found is not None
    assert found.id == opened.id


def test_shift_repo_overlapping_only_matches_open_or_submitted_timesheets(
    db: Session, seeded: dict[str, Worker]
) -> None:
    worker_id = seeded["priya"].id
    period = date(2026, 1, 5)
    timesheets = TimesheetRepo(db)
    shifts = ShiftRepo(db)

    open_sheet = timesheets.add(
        Timesheet(worker_id=worker_id, period_start=period, version=1, status="open")
    )
    approved_sheet = timesheets.add(
        Timesheet(worker_id=worker_id, period_start=period, version=2, status="approved")
    )
    existing_start = datetime(2026, 1, 5, 9, tzinfo=UTC)
    existing_end = datetime(2026, 1, 5, 17, tzinfo=UTC)
    shifts.add(
        Shift(
            timesheet_id=open_sheet.id,
            start_utc=existing_start,
            end_utc=existing_end,
            minutes=480,
        )
    )
    shifts.add(
        Shift(
            timesheet_id=approved_sheet.id,
            start_utc=existing_start,
            end_utc=existing_end,
            minutes=480,
        )
    )

    overlapping = shifts.overlapping(
        worker_id, datetime(2026, 1, 5, 16, tzinfo=UTC), datetime(2026, 1, 5, 18, tzinfo=UTC)
    )
    # Only the shift on the open timesheet counts; the approved one is out of scope.
    assert len(overlapping) == 1
    assert overlapping[0].timesheet_id == open_sheet.id

    no_overlap = shifts.overlapping(
        worker_id, datetime(2026, 1, 5, 17, tzinfo=UTC), datetime(2026, 1, 5, 18, tzinfo=UTC)
    )
    assert list(no_overlap) == []
