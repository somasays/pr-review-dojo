"""Hidden tests for exercise 36: recurring bookings and a room waitlist.

Each test below fails against ex/36-recurring-bookings-waitlist and passes
against solutions/36.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sandbox.rooms.db import Room
from sandbox.rooms.domain.slots import Slot, split_credit_cents
from sandbox.rooms.repo import BookingRepo, RoomRepo, WaitlistRepo
from sandbox.rooms.service import BookingService, Conflict
from sandbox.rooms.tests.conftest import HOLDER_EMAIL, TEST_API_KEY

API_ROOT = Path("sandbox/rooms/api.py")


# --- Domain: split_credit_cents drops a cent when the credit does not ------
# --- divide evenly across the series (SV-12). ------------------------------


def test_split_credit_cents_sums_to_the_original_amount() -> None:
    shares = split_credit_cents(1000, 3)
    assert sum(shares) == 1000
    assert shares == [334, 333, 333]


# --- Service: book_recurring commits earlier weeks before a later week -----
# --- conflict is discovered, breaking the all-or-nothing promise (SV-02). --


def test_book_recurring_is_all_or_nothing_on_a_later_week_conflict(db: object, room: Room) -> None:
    service = BookingService(RoomRepo(db), BookingRepo(db), WaitlistRepo(db))
    first = Slot(datetime(2026, 9, 8, 9, tzinfo=UTC), datetime(2026, 9, 8, 10, tzinfo=UTC))
    third_week = Slot(first.start + timedelta(weeks=2), first.end + timedelta(weeks=2))
    service.book(room.id, "carl@example.com", third_week, member=False)

    with pytest.raises(Conflict):
        service.book_recurring(room.id, "ada@example.com", first, weeks=4, member=False)

    assert service.bookings.find_conflicts(room.id, first) == []


# --- Repository: WaitlistRepo used to pick the earliest waiter for the -----
# --- room regardless of which slot they asked for (SA-16). -----------------


def test_cancel_only_promotes_a_holder_waiting_for_the_freed_slot(db: object, room: Room) -> None:
    service = BookingService(RoomRepo(db), BookingRepo(db), WaitlistRepo(db))
    slot_x = Slot(datetime(2026, 9, 8, 9, tzinfo=UTC), datetime(2026, 9, 8, 10, tzinfo=UTC))
    slot_y = Slot(datetime(2026, 9, 10, 14, tzinfo=UTC), datetime(2026, 9, 10, 15, tzinfo=UTC))

    service.book(room.id, "occupant@example.com", slot_x, member=False)
    # a waits for X (already taken) and joins first; b waits for Y.
    service.join_waitlist(room.id, "a@example.com", slot_x, member=False)
    service.join_waitlist(room.id, "b@example.com", slot_y, member=False)

    booking_y = service.book(room.id, "holder@example.com", slot_y, member=False)
    service.cancel(booking_y.id, "holder@example.com")

    active_x = service.bookings.find_conflicts(room.id, slot_x)
    assert [b.holder_email for b in active_x] == ["occupant@example.com"]

    active_y = service.bookings.find_conflicts(room.id, slot_y)
    assert [b.holder_email for b in active_y] == ["b@example.com"]


# --- API: join_waitlist used to trust a client-supplied holder_email over --
# --- the authenticated principal (FA-01). -----------------------------------


def test_join_waitlist_ignores_a_client_supplied_holder_email(
    client: object, room: Room, db: object
) -> None:
    resp = client.post(
        f"/rooms/{room.id}/waitlist",
        json={
            "start": "2026-09-08T09:00:00Z",
            "end": "2026-09-08T10:00:00Z",
            "holder_email": "victim@example.com",
        },
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert resp.status_code == 201
    entries = WaitlistRepo(db).list_for_room(room.id)
    assert [e.holder_email for e in entries] == [HOLDER_EMAIL]


# --- Design: the waitlist listing endpoint used to build its own select() --
# --- instead of going through WaitlistRepo (DS-05). -------------------------


def test_waitlist_listing_goes_through_the_repo_not_a_raw_query() -> None:
    source = API_ROOT.read_text()
    assert "from sqlalchemy import select" not in source
    assert "WaitlistRepo(db).list_for_room" in source


# --- Design: recurring_slots shipped with no direct test (DS-22). ----------


def test_recurring_slots_has_a_direct_test() -> None:
    source = Path("sandbox/rooms/tests/test_slots.py").read_text()
    assert "recurring_slots(" in source


# --- Refactor: create_booking and create_recurring_booking duplicated ------
# --- their NotFound/Conflict mapping instead of sharing a helper (DS-20). --


def test_booking_endpoints_share_one_error_mapping_helper() -> None:
    tree = ast.parse(API_ROOT.read_text())
    helper_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_")
    }
    calls_by_func: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {
            "create_booking",
            "create_recurring_booking",
        }:
            calls_by_func[node.name] = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id in helper_names
            }
    shared = calls_by_func["create_booking"] & calls_by_func["create_recurring_booking"]
    assert shared
