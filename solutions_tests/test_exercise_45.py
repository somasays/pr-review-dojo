"""Hidden tests for exercise 45: partial claim approval.

Each test below fails against ex/45-partial-approval and passes against
solutions/45.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sandbox.expenses.domain.policy import (
    Category,
    ClaimStatus,
    LineRejection,
    PolicyLimit,
    claim_outcome,
    payable_total,
)
from sandbox.expenses.service import ClaimService, LineInput, NotAllowed
from solutions_tests.conftest import APPROVER_EMAIL, EMPLOYEE_EMAIL, SELF_APPROVER_EMAIL

SERVICE_ROOT = Path("sandbox/expenses/service.py")
INCURRED = date(2026, 6, 15)


# --- Domain arithmetic: payable_total treated the intermediate "cents"
# --- value as if it were already dollars and never scaled it back down
# --- (LG-01). ---------------------------------------------------------------


def test_payable_total_sums_the_approved_amounts() -> None:
    total = payable_total([Decimal("20.00"), Decimal("5.00")])
    assert total == Decimal("25.00")


# --- Domain: claim_outcome rejected the whole claim whenever any line was
# --- rejected instead of only when every line was rejected (LG-12). --------


def test_claim_outcome_approved_when_not_all_lines_rejected() -> None:
    assert claim_outcome(1, 2) is ClaimStatus.APPROVED


def test_claim_outcome_rejected_when_every_line_is_rejected() -> None:
    assert claim_outcome(0, 2) is ClaimStatus.REJECTED


# --- Service: the self-approval check only ran on the "reject the whole
# --- claim or none of it" branch, so deciding through the new line
# --- rejection path skipped it entirely (FA-03). ----------------------------


def test_self_approval_is_blocked_even_with_line_rejections(session_factory, seeded) -> None:  # noqa: ANN001
    service = ClaimService(session_factory)
    claim = service.submit(
        SELF_APPROVER_EMAIL,
        "hidden-key-1",
        "USD",
        [
            LineInput(Category.MEALS, Decimal("20.00"), INCURRED),
            LineInput(Category.MEALS, Decimal("15.00"), INCURRED),
        ],
    )
    with pytest.raises(NotAllowed):
        service.decide(
            SELF_APPROVER_EMAIL,
            claim.id,
            True,
            None,
            rejected_lines=[LineRejection(claim.lines[0].id, "missing receipt")],
        )


# --- Repository: the month-cap re-check reused month_total_for, which still
# --- counts the claim being decided (still "submitted" at query time) in
# --- full, double-counting it against the freshly approved amount instead
# --- of checking other already-approved claims (SV-12). --------------------


def test_month_cap_recheck_does_not_double_count_the_claim_being_decided(
    session_factory,  # noqa: ANN001
    seeded,  # noqa: ANN001
) -> None:
    tight = {Category.MEALS: PolicyLimit(Category.MEALS, Decimal("50.00"), Decimal("50.00"))}
    service = ClaimService(session_factory, limits=tight)
    claim = service.submit(
        EMPLOYEE_EMAIL, "hidden-key-2", "USD", [LineInput(Category.MEALS, Decimal("50.00"), INCURRED)]
    )
    decided = service.decide(APPROVER_EMAIL, claim.id, True, None)
    assert decided.status == "approved"


# --- Design: decide's rejected_lines parameter traveled as raw
# --- (line id, reason) tuples instead of a small domain type (DS-13). ------


def test_decide_rejected_lines_param_uses_a_domain_type() -> None:
    source = SERVICE_ROOT.read_text()
    assert "tuple[str, str]" not in source
    assert "LineRejection" in source


# --- Design: line outcomes were compared and assigned as bare "approved"
# --- and "rejected" string literals instead of going through an enum,
# --- unlike every other status comparison in this codebase (DS-14). --------


def test_no_bare_outcome_string_literals_in_service() -> None:
    source = SERVICE_ROOT.read_text()
    assert '"approved"' not in source
    assert '"rejected"' not in source
