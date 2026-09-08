"""Hidden tests for exercise 41: booking amendment.

Each test below fails against ex/41-booking-amendment and passes against
solutions/41.
"""

from __future__ import annotations

import ast
import typing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sandbox.rooms.db import Room
from sandbox.rooms.domain.slots import AMEND_CUTOFF, Slot, can_amend
from sandbox.rooms.repo import BookingRepo, RoomRepo
from sandbox.rooms.service import BookingService, Conflict
from solutions_tests.conftest import HOLDER_EMAIL, TEST_API_KEY

API_ROOT = Path("sandbox/rooms/api.py")
TESTS_ROOT = Path("sandbox/rooms/tests")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_half_hour(dt: datetime) -> datetime:
    aligned = dt.replace(minute=0, second=0, microsecond=0)
    while aligned <= dt:
        aligned += timedelta(minutes=30)
    return aligned


# --- API: amend_booking used to skip the ownership check (FA-03) -----------


def test_amend_refuses_a_non_holder(client, room: Room, monkeypatch) -> None:  # noqa: ANN001
    start = _next_half_hour(datetime.now(UTC) + timedelta(hours=3))
    created = client.post(
        "/bookings",
        json={"room_id": room.id, "start": _iso(start), "end": _iso(start + timedelta(hours=1))},
        headers={"X-API-Key": TEST_API_KEY},
    )
    booking_id = created.json()["id"]

    monkeypatch.setenv("ROOMS_API_KEYS", f"{HOLDER_EMAIL}:{TEST_API_KEY},eve@example.com:eve-key")
    new_start = _next_half_hour(datetime.now(UTC) + timedelta(hours=6))
    resp = client.patch(
        f"/bookings/{booking_id}/amend",
        json={"start": _iso(new_start), "end": _iso(new_start + timedelta(hours=1))},
        headers={"X-API-Key": "eve-key"},
    )
    assert resp.status_code == 403


# --- Service: the conflict check ran after the slot/price write already ---
# --- committed (SV-02). -----------------------------------------------------


def test_amend_does_not_persist_a_conflicting_slot_when_refused(
    session_factory, room: Room
) -> None:  # noqa: ANN001
    setup = session_factory()
    try:
        service = BookingService(RoomRepo(setup), BookingRepo(setup))
        now = datetime.now(UTC)
        start_a = _next_half_hour(now + timedelta(hours=3))
        booking_a = service.book(
            room.id, HOLDER_EMAIL, Slot(start_a, start_a + timedelta(minutes=30)), member=False
        )
        start_b = _next_half_hour(now + timedelta(hours=5))
        service.book(
            room.id, "bea@example.com", Slot(start_b, start_b + timedelta(minutes=30)), member=False
        )
        booking_id = booking_a.id
        original_start = booking_a.start
    finally:
        setup.close()

    amend_session = session_factory()
    try:
        service = BookingService(RoomRepo(amend_session), BookingRepo(amend_session))
        try:
            service.amend(
                booking_id,
                HOLDER_EMAIL,
                Slot(start_b, start_b + timedelta(minutes=30)),
                member=False,
            )
        except Conflict:
            pass
        else:
            raise AssertionError("expected a Conflict")
    finally:
        amend_session.close()

    check = session_factory()
    try:
        reloaded = BookingRepo(check).get(booking_id)
        assert reloaded is not None
        assert reloaded.start.replace(tzinfo=UTC) == original_start
    finally:
        check.close()


# --- Repository: find_conflicts_excluding used to count a cancelled -------
# --- booking as still occupying the slot (SA-16). ---------------------------


def test_amend_ignores_a_cancelled_booking_in_the_target_slot(db, room: Room) -> None:  # noqa: ANN001
    service = BookingService(RoomRepo(db), BookingRepo(db))
    now = datetime.now(UTC)
    start_a = _next_half_hour(now + timedelta(hours=3))
    booking_a = service.book(
        room.id, HOLDER_EMAIL, Slot(start_a, start_a + timedelta(minutes=30)), member=False
    )
    start_b = _next_half_hour(now + timedelta(hours=5))
    booking_b = service.book(
        room.id, "bea@example.com", Slot(start_b, start_b + timedelta(minutes=30)), member=False
    )
    service.cancel(booking_b.id, "bea@example.com")

    updated, _ = service.amend(
        booking_a.id, HOLDER_EMAIL, Slot(start_b, start_b + timedelta(minutes=30)), member=False
    )
    assert updated.start == start_b


# --- Domain: can_amend turned the cutoff boundary exclusive (LG-12). -------


def test_can_amend_boundary_is_inclusive() -> None:
    now = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    assert can_amend(now, now + AMEND_CUTOFF)


# --- Design: can_amend shipped with no direct test (DS-22). ----------------


def test_can_amend_has_a_direct_test() -> None:
    found = any("can_amend(" in path.read_text() for path in TESTS_ROOT.glob("test_*.py"))
    assert found


# --- Design: amend took raw start/end datetimes instead of a Slot ---------
# --- (DS-13). -----------------------------------------------------------------


def test_amend_signature_takes_a_slot_not_raw_datetimes() -> None:
    hints = typing.get_type_hints(BookingService.amend)
    assert datetime not in hints.values()


# --- Refactor: the three booking endpoints each mapped service errors to --
# --- HTTPException by hand instead of sharing one helper (DS-20). ----------


def test_booking_endpoints_share_one_error_mapping_helper() -> None:
    tree = ast.parse(API_ROOT.read_text())
    endpoint_names = {"create_booking", "cancel_booking", "amend_booking"}
    calls_helper = dict.fromkeys(endpoint_names, False)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in endpoint_names:
            calls_helper[node.name] = any(
                isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "_as_http_error"
                for inner in ast.walk(node)
            )
    assert all(calls_helper.values()), calls_helper
