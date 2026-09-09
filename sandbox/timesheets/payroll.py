"""CSV export for a pay period's approved timesheets.

export_period is idempotent: it always rewrites the whole file from the
database, writing to a temporary file in the same directory first and
renaming it into place, so a crash mid-write never leaves a truncated file
and re-running for the same period reproduces the same output.
"""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from sandbox.timesheets.db import Timesheet, Worker
from sandbox.timesheets.domain.pay import TimesheetStatus
from sandbox.timesheets.repo import ShiftRepo
from sandbox.timesheets.service import DEFAULT_RULES, period_totals


def export_period(
    session_factory: sessionmaker[Session], period_start: date, path: str | Path
) -> int:
    """Write one row per approved timesheet for `period_start` to `path`,
    the latest version among that worker's approved versions only.

    Columns are worker_email, regular_minutes, overtime_minutes,
    night_minutes, total_pay. Returns the number of rows written.
    """
    path = Path(path)
    session = session_factory()
    try:
        latest_approved = (
            select(Timesheet.worker_id, func.max(Timesheet.version).label("version"))
            .where(
                Timesheet.period_start == period_start,
                Timesheet.status == TimesheetStatus.APPROVED.value,
            )
            .group_by(Timesheet.worker_id)
            .subquery()
        )
        stmt = (
            select(Timesheet, Worker)
            .join(Worker, Timesheet.worker_id == Worker.id)
            .join(
                latest_approved,
                (Timesheet.worker_id == latest_approved.c.worker_id)
                & (Timesheet.version == latest_approved.c.version),
            )
            .where(Timesheet.period_start == period_start)
            .order_by(Worker.email)
        )
        rows = session.execute(stmt).all()

        shift_repo = ShiftRepo(session)
        csv_rows: list[tuple[str, int, int, int, str]] = []
        for timesheet, worker in rows:
            shifts = shift_repo.for_timesheet(timesheet.id)
            regular, overtime, night = period_totals(shifts, worker.timezone, DEFAULT_RULES)
            csv_rows.append((worker.email, regular, overtime, night, str(timesheet.total_pay)))
    finally:
        session.close()

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "worker_email",
                    "regular_minutes",
                    "overtime_minutes",
                    "night_minutes",
                    "total_pay",
                ]
            )
            writer.writerows(csv_rows)
        os.replace(tmp_name, path)
    except Exception:
        os.unlink(tmp_name)
        raise
    return len(csv_rows)
