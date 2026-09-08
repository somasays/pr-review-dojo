"""Hidden tests for exercise 39: loan renewals.

Each test below fails against ex/39-loan-renewals and passes against
solutions/39.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sandbox.library.db import Hold, session_scope
from sandbox.library.repo import LoanRepo
from sandbox.library.service import MAX_RENEWALS, LendingService, NotAllowed
from solutions_tests.conftest import OTHER_PATRON_KEY, PATRON_KEY

API_ROOT = Path("sandbox/library/api.py")
SERVICE_ROOT = Path("sandbox/library/service.py")
TESTS_ROOT = Path("sandbox/library/tests")


# --- Domain arithmetic: renewal count boundary (LG-12) ---------------------


def test_renew_refuses_past_the_configured_maximum(db, seeded):  # noqa: ANN001
    service = LendingService(db)
    alice, book = seeded["alice"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))

    for _ in range(MAX_RENEWALS):
        service.renew_loan(loan.id, alice.email, date(2024, 1, 10))

    with pytest.raises(NotAllowed):
        service.renew_loan(loan.id, alice.email, date(2024, 1, 10))


# --- Service: a premature commit lets a refused renewal's mutation stick ---
# --- around (SV-02). ---------------------------------------------------------


def test_renew_does_not_persist_partial_state_when_a_hold_blocks_it(session_factory, seeded):  # noqa: ANN001
    session = session_factory()
    try:
        loan = LendingService(session).checkout(
            seeded["alice"].email, seeded["book"].id, date(2024, 1, 1)
        )
        session.commit()
        loan_id, original_due_on = loan.id, loan.due_on
    finally:
        session.close()

    hold_session = session_factory()
    try:
        LendingService(hold_session).place_hold(
            seeded["bob"].email, seeded["book"].id, datetime.now(UTC)
        )
        hold_session.commit()
    finally:
        hold_session.close()

    with pytest.raises(NotAllowed), session_scope(session_factory) as renewing_session:
        LendingService(renewing_session).renew_loan(
            loan_id, seeded["alice"].email, date(2024, 1, 10)
        )

    check = session_factory()
    try:
        reloaded = LoanRepo(check).get(loan_id)
        assert reloaded is not None
        assert reloaded.due_on == original_due_on
        assert reloaded.renewals == 0
    finally:
        check.close()


# --- Repository: other_patron_holds used to count a fulfilled hold as ------
# --- still blocking a renewal (SA-16). --------------------------------------


def test_renew_ignores_a_fulfilled_hold_from_another_patron(db, seeded):  # noqa: ANN001
    service = LendingService(db)
    alice, bob, book = seeded["alice"], seeded["bob"], seeded["book"]
    loan = service.checkout(alice.email, book.id, date(2024, 1, 1))
    hold = service.holds.add(Hold(item_id=book.id, patron_id=bob.id, placed_at=datetime.now(UTC)))
    service.holds.fulfill(hold.id, datetime.now(UTC))

    renewed = service.renew_loan(loan.id, alice.email, date(2024, 1, 10))

    assert renewed.renewals == 1


# --- API: renew_loan used to honor a client-supplied patron email without --
# --- checking the caller was a librarian (FA-01). ---------------------------


def test_renew_endpoint_ignores_on_behalf_of_for_a_patron_key(client, seeded):  # noqa: ANN001
    alice, book = seeded["alice"], seeded["book"]
    created = client.post(
        "/loans", json={"item_id": book.id}, headers={"X-Library-Key": PATRON_KEY}
    )
    loan_id = created.json()["id"]

    response = client.post(
        f"/loans/{loan_id}/renew",
        json={"on_behalf_of_patron_email": alice.email},
        headers={"X-Library-Key": OTHER_PATRON_KEY},
    )

    assert response.status_code == 403


# --- Design: the lost-loan check compared against a string literal instead -
# --- of the LoanStatus enum (DS-14). ----------------------------------------


def test_lost_status_check_uses_the_enum_not_a_string_literal() -> None:
    source = SERVICE_ROOT.read_text()
    assert '"lost"' not in source


# --- Design: HoldRepo.other_patron_holds shipped with no direct test -------
# --- (DS-22). -----------------------------------------------------------------


def test_other_patron_holds_has_a_direct_test() -> None:
    found = any("other_patron_holds(" in path.read_text() for path in TESTS_ROOT.glob("test_*.py"))
    assert found


# --- Refactor: the four loan/hold endpoints each mapped NotFound/NotAllowed
# --- to HTTPException by hand instead of sharing one helper (DS-20). -------


def test_loan_endpoints_share_one_error_mapping_helper() -> None:
    tree = ast.parse(API_ROOT.read_text())
    endpoint_names = {"create_loan", "return_loan", "renew_loan", "create_hold"}
    calls_helper = dict.fromkeys(endpoint_names, False)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in endpoint_names:
            calls_helper[node.name] = any(
                isinstance(inner, ast.Call)
                and getattr(inner.func, "id", None) == "_reraise_as_http"
                for inner in ast.walk(node)
            )
    assert all(calls_helper.values()), calls_helper
