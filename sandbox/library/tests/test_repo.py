"""Direct tests for repository methods that are new enough to have no
coverage except through the services that call them."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from sandbox.library.db import Hold, Item, Patron
from sandbox.library.repo import HoldRepo


def _hold(item_id: int, patron_id: int) -> Hold:
    return Hold(item_id=item_id, patron_id=patron_id, placed_at=datetime.now(UTC))


def test_other_patron_holds_ignores_the_renewing_patron_s_own_hold(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    alice, book = seeded["alice"], seeded["book"]
    holds = HoldRepo(db)
    holds.add(_hold(book.id, alice.id))

    assert holds.other_patron_holds(book.id, alice.id) is False


def test_other_patron_holds_ignores_a_fulfilled_hold(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    alice, bob, book = seeded["alice"], seeded["bob"], seeded["book"]
    holds = HoldRepo(db)
    hold = holds.add(_hold(book.id, bob.id))
    holds.fulfill(hold.id, datetime.now(UTC))

    assert holds.other_patron_holds(book.id, alice.id) is False


def test_other_patron_holds_true_for_an_active_hold_from_someone_else(
    db: Session, seeded: dict[str, Patron | Item]
) -> None:
    alice, bob, book = seeded["alice"], seeded["bob"], seeded["book"]
    holds = HoldRepo(db)
    holds.add(_hold(book.id, bob.id))

    assert holds.other_patron_holds(book.id, alice.id) is True
