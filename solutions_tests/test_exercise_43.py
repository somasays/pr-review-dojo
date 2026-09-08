"""Hidden tests for exercise 43: lost items.

Each test below fails against ex/43-lost-items and passes against
solutions/43.
"""

from __future__ import annotations

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sandbox.library.db import session_scope
from sandbox.library.domain.lending import InvalidTransition, replacement_fee_for
from sandbox.library.repo import ItemRepo
from sandbox.library.service import LendingService
from solutions_tests.conftest import PATRON_KEY

API_ROOT = Path("sandbox/library/api.py")
SERVICE_ROOT = Path("sandbox/library/service.py")
TESTS_ROOT = Path("sandbox/library/tests")


# --- Domain arithmetic: the cap was applied to the replacement cost alone,
# --- so a fine on top of it could push the fee past the configured max
# --- (LG-03). ----------------------------------------------------------------


def test_replacement_fee_caps_the_combined_total() -> None:
    fee = replacement_fee_for(Decimal("60.00"), Decimal("20.00"), Decimal("70.00"))
    assert fee == Decimal("70.00")


# --- Service: report_lost committed the copy reduction before the status
# --- transition, so a second report against the same loan durably drained
# --- another copy before the transition finally refused it (SV-02). --------


def test_report_lost_twice_does_not_double_decrement_copies(session_factory, seeded):  # noqa: ANN001
    with session_scope(session_factory) as session:
        loan = LendingService(session).checkout(
            seeded["alice"].email, seeded["book"].id, date(2024, 1, 1)
        )
        loan_id = loan.id

    with session_scope(session_factory) as session:
        LendingService(session).report_lost(
            loan_id, "patron", seeded["alice"].email, date(2024, 1, 5)
        )

    with pytest.raises(InvalidTransition), session_scope(session_factory) as session:
        LendingService(session).report_lost(
            loan_id, "patron", seeded["alice"].email, date(2024, 1, 6)
        )

    check = session_factory()
    try:
        assert ItemRepo(check).get(seeded["book"].id).copies == 0
    finally:
        check.close()


# --- Repository: available_copies subtracted lost loans on top of the copy
# --- count that report_lost already permanently reduced, double-counting
# --- the loss (SA-16). --------------------------------------------------------


def test_available_copies_not_reduced_twice_for_a_lost_loan(db, seeded):  # noqa: ANN001
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    db.commit()

    service.report_lost(loan.id, "patron", alice.email, date(2024, 1, 2))
    db.commit()

    assert service.items.available_copies(book.id) == 0


# --- API: reverse_loss accepted any valid key instead of a librarian key
# --- (FA-03). ------------------------------------------------------------------


def test_reverse_loss_endpoint_refuses_a_patron_key(client, seeded):  # noqa: ANN001
    book = seeded["other_book"]
    patron_headers = {"X-Library-Key": PATRON_KEY}
    loan = client.post("/loans", json={"item_id": book.id}, headers=patron_headers).json()
    client.post(f"/loans/{loan['id']}/report-lost", headers=patron_headers)

    response = client.post(f"/loans/{loan['id']}/reverse-loss", headers=patron_headers)

    assert response.status_code == 403


# --- Design: the lost-loan check compared against a string literal instead
# --- of the LoanStatus enum (DS-14). ------------------------------------------


def test_lost_status_check_uses_the_enum_not_a_string_literal() -> None:
    source = SERVICE_ROOT.read_text()
    assert '"lost"' not in source


# --- Design: can_reverse_loss shipped with no direct test (DS-22). ---------


def test_can_reverse_loss_has_a_direct_test() -> None:
    found = any("can_reverse_loss(" in path.read_text() for path in TESTS_ROOT.glob("test_*.py"))
    assert found


# --- Refactor: the lost-item endpoints mapped NotFound/NotAllowed to
# --- HTTPException by hand instead of sharing one helper (DS-20). ----------


def test_lost_item_endpoints_share_one_error_mapping_helper() -> None:
    tree = ast.parse(API_ROOT.read_text())
    endpoint_names = {"report_lost", "reverse_loss"}
    uses_helper = dict.fromkeys(endpoint_names, False)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in endpoint_names:
            uses_helper[node.name] = any(
                isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "_service_errors"
                for inner in ast.walk(node)
            )
    assert all(uses_helper.values()), uses_helper
