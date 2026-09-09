from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db.backfill_customer_names import backfill_batch
from app.db.models import Customer, CustomerNameBackfill
from app.domain.names import join_name, split_full_name


@pytest.mark.parametrize(
    ("display_name", "expected"),
    [
        ("Ada Lovelace", ("Ada", "Lovelace")),
        ("Grace Brewster Murray Hopper", ("Grace Brewster Murray", "Hopper")),
        ("Prince", ("Prince", "")),
        ("  Alan   Turing  ", ("Alan", "Turing")),
        ("Sammy Davis Jr.", ("Sammy", "Davis Jr.")),
        ("", ("", "")),
    ],
)
def test_split_full_name(display_name: str, expected: tuple[str, str]) -> None:
    assert split_full_name(display_name) == expected


def test_join_name_round_trip() -> None:
    first_name, last_name = split_full_name("Ada Lovelace")
    assert join_name(first_name, last_name) == "Ada Lovelace"
    assert join_name("Prince", "") == "Prince"


def test_backfill_batch_splits_and_audits(db: Session) -> None:
    db.add_all(
        [
            Customer(email="a@example.com", name="Ada Lovelace"),
            Customer(email="b@example.com", name="Prince"),
        ]
    )
    db.commit()

    touched = backfill_batch(db, batch_size=10)
    db.commit()

    assert touched == 2
    rows = {c.email: c for c in db.query(Customer).all()}
    assert (rows["a@example.com"].first_name, rows["a@example.com"].last_name) == (
        "Ada",
        "Lovelace",
    )
    assert (rows["b@example.com"].first_name, rows["b@example.com"].last_name) == ("Prince", "")
    assert db.query(CustomerNameBackfill).count() == 2


def test_backfill_batch_is_resumable(db: Session) -> None:
    db.add(Customer(email="c@example.com", name="Alan Turing"))
    db.commit()

    assert backfill_batch(db, batch_size=10) == 1
    db.commit()
    assert backfill_batch(db, batch_size=10) == 0


def test_create_customer_returns_split_name(client) -> None:
    from conftest import ADMIN_KEY

    resp = client.post(
        "/customers",
        headers={"X-API-Key": ADMIN_KEY},
        json={"email": "new@example.com", "name": "Katherine Johnson"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["first_name"] == "Katherine"
    assert body["last_name"] == "Johnson"
    assert body["name"] == "Katherine Johnson"
