"""Hidden tests for exercise 50: shift splitting at local midnight.

Each test below fails against ex/50-midnight-shift-split and passes
against solutions/50.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from sandbox.timesheets.db import Timesheet
from sandbox.timesheets.domain.pay import local_midnight_after
from sandbox.timesheets.repo import TimesheetRepo
from sandbox.timesheets.service import Overlap, ShiftService
from sandbox.timesheets.tests.conftest import WORKER_EMAIL, WORKER_KEY

SERVICE_PY = Path("sandbox/timesheets/service.py")
TESTS_DIR = Path("sandbox/timesheets/tests")


def _la_utc(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    local = datetime(y, m, d, h, mi, tzinfo=ZoneInfo("America/Los_Angeles"))
    return local.astimezone(UTC)


# --- Domain: the split boundary is computed by adding a fixed 24 hours ----
# --- to the UTC instant instead of resolving the true local midnight, so --
# --- it drifts by an hour on a DST transition day. -------------------------


def test_local_midnight_after_on_the_dst_spring_forward_day() -> None:
    start = datetime(2026, 3, 8, 1, 0, tzinfo=ZoneInfo("America/Los_Angeles")).astimezone(UTC)
    boundary = local_midnight_after(start, "America/Los_Angeles")
    expected = datetime(2026, 3, 9, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles")).astimezone(UTC)
    assert boundary == expected


# --- Service: the overlap check is narrowed to the first part's span, so --
# --- a shift that only conflicts with the post-midnight part is missed. ---


def test_overlap_check_covers_the_whole_split_shift(session_factory, seeded, db) -> None:
    service = ShiftService(session_factory)
    service.record(WORKER_EMAIL, _la_utc(2026, 1, 6, 1), _la_utc(2026, 1, 6, 3), None)

    with pytest.raises(Overlap):
        service.record(WORKER_EMAIL, _la_utc(2026, 1, 5, 22), _la_utc(2026, 1, 6, 2), None)


# --- Repository: open_for_period does not filter by status, so it can -----
# --- hand back a submitted or approved (frozen) timesheet. ----------------


def test_open_for_period_ignores_a_frozen_timesheet(db, seeded) -> None:
    worker_id = seeded["priya"].id
    period = date(2026, 1, 5)
    repo = TimesheetRepo(db)
    repo.add(Timesheet(worker_id=worker_id, period_start=period, version=1, status="submitted"))

    assert repo.open_for_period(worker_id, period) is None


# --- API: the response for a split shift reports only the first part's ----
# --- minutes instead of the whole shift the worker clocked. ---------------


def test_create_shift_response_reports_the_combined_minutes_for_a_split(client) -> None:
    resp = client.post(
        "/shifts",
        json={
            "start_utc": "2026-01-05T22:00:00-08:00",
            "end_utc": "2026-01-06T02:00:00-08:00",
            "note": None,
        },
        headers={"X-Timesheets-Key": WORKER_KEY},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["group_id"] is not None
    assert body["minutes"] == 240


# --- Design: local_midnight_after is new and public, but nothing in the ---
# --- shipped test suite calls it directly. ---------------------------------


def test_local_midnight_after_has_a_direct_test() -> None:
    found = any("local_midnight_after(" in path.read_text() for path in TESTS_DIR.glob("test_*.py"))
    assert found


# --- Design: the second part's period start is recomputed inline instead --
# --- of reusing the existing period_start_for helper. ----------------------


def test_second_period_start_reuses_period_start_for() -> None:
    text = SERVICE_PY.read_text()
    assert text.count(".weekday()") == 1


# --- Refactor: the two split parts are built by two near-identical Shift --
# --- constructor calls instead of one small shared helper. -----------------


def test_shift_rows_built_through_one_helper() -> None:
    tree = ast.parse(SERVICE_PY.read_text())
    record_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ShiftService":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "record":
                    record_fn = item
    assert record_fn is not None

    direct_shift_calls = [
        n
        for n in ast.walk(record_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Shift"
    ]
    assert direct_shift_calls == []
