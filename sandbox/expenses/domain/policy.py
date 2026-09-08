"""Category limits, currency conversion, and claim status: pure logic with
no IO.

See sandbox/expenses/README.md for why this package's conventions (UUID4
string ids, Decimal money quantized to cents with an explicit currency code,
a single transition function for claim status) differ from app/domain,
sandbox/rooms/domain, sandbox/lockers/domain, and sandbox/library/domain.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Category(Enum):
    MEALS = "meals"
    TRAVEL = "travel"
    LODGING = "lodging"
    EQUIPMENT = "equipment"


@dataclass(frozen=True, slots=True)
class PolicyLimit:
    category: Category
    per_line_max: Decimal
    per_month_max: Decimal


def line_violations(
    line_amount: Decimal, category: Category, limits: Mapping[Category, PolicyLimit]
) -> list[str]:
    """Reasons `line_amount` breaks policy for `category`, if any.

    A category with no entry in `limits` has no per-line cap and never
    violates. Returns an empty list when the line is within policy; a line
    exactly at the cap is within policy.
    """
    limit = limits.get(category)
    if limit is None or line_amount <= limit.per_line_max:
        return []
    return [
        f"{category.value} line of {line_amount} exceeds the per-line max of {limit.per_line_max}"
    ]


def month_total_ok(
    existing_month_total: Decimal, new_amount: Decimal, limit: PolicyLimit | None
) -> bool:
    """Whether adding `new_amount` to `existing_month_total` stays within the
    category's monthly cap.

    A category with no limit configured is always ok. A total that lands
    exactly on the cap is ok.
    """
    if limit is None:
        return True
    return existing_month_total + new_amount <= limit.per_month_max


def convert(amount: Decimal, rate: Decimal, precision: int) -> Decimal:
    """Convert `amount` by `rate`, quantized to `precision` decimal places,
    rounding half up."""
    if precision < 0:
        raise ValueError("precision must not be negative")
    quantum = Decimal(1).scaleb(-precision)
    return (amount * rate).quantize(quantum, rounding=ROUND_HALF_UP)


def payable_total(approved_amounts: Sequence[Decimal]) -> Decimal:
    """Sum of the approved line amounts on a claim, quantized to cents.

    Rejected lines are never passed in; a claim with no approved lines has
    a payable total of zero.
    """
    total = sum(approved_amounts, Decimal("0.00"))
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class ClaimStatus(Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class InvalidTransition(Exception):
    pass


# The only moves a claim's status is allowed to make. Rejected and paid are
# terminal: once a claim reaches either, it never changes status again.
_ALLOWED_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.DRAFT: frozenset({ClaimStatus.SUBMITTED}),
    ClaimStatus.SUBMITTED: frozenset({ClaimStatus.APPROVED, ClaimStatus.REJECTED}),
    ClaimStatus.APPROVED: frozenset({ClaimStatus.PAID}),
    ClaimStatus.REJECTED: frozenset(),
    ClaimStatus.PAID: frozenset(),
}


def transition(current: ClaimStatus, target: ClaimStatus) -> ClaimStatus:
    """The only way a claim's status is allowed to change.

    Returns `target` when the move is legal, raises InvalidTransition
    otherwise. Every place in this codebase that changes a claim's status
    goes through this function.
    """
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(f"cannot move a claim from {current.value} to {target.value}")
    return target


def claim_outcome(approved_line_count: int, total_line_count: int) -> ClaimStatus:
    """The status a decided claim lands in: rejected only when every line on
    it was rejected, approved when at least one line survives.
    """
    if approved_line_count == 0:
        return ClaimStatus.REJECTED
    return ClaimStatus.APPROVED
