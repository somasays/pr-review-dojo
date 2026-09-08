"""Tests for sandbox/expenses/export.py."""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from sandbox.expenses.domain.policy import Category
from sandbox.expenses.export import write_batch_csv
from sandbox.expenses.service import ClaimService, LineInput, PayoutService
from sandbox.expenses.tests.conftest import EMPLOYEE_EMAIL, SELF_APPROVER_EMAIL


def _paid_batch(session_factory: sessionmaker[Session]) -> str:
    claims = ClaimService(session_factory)
    payouts = PayoutService(session_factory)
    line = LineInput(Category.MEALS, Decimal("20.00"), date(2026, 6, 15))
    claim = claims.submit(EMPLOYEE_EMAIL, "export-key-1", "USD", [line])
    claims.decide(SELF_APPROVER_EMAIL, claim.id, True, None)
    batch = payouts.create_batch(datetime.now(UTC))
    return batch.id


def test_write_batch_csv_writes_expected_rows(
    session_factory: sessionmaker[Session], seeded, tmp_path: Path
) -> None:
    batch_id = _paid_batch(session_factory)
    out = tmp_path / "batch.csv"
    count = write_batch_csv(batch_id, out, session_factory)
    assert count == 1
    with out.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["employee_email"] == EMPLOYEE_EMAIL
    assert rows[0]["currency"] == "USD"
    assert rows[0]["total"] == "20.00"


def test_write_batch_csv_is_idempotent(
    session_factory: sessionmaker[Session], seeded, tmp_path: Path
) -> None:
    batch_id = _paid_batch(session_factory)
    out = tmp_path / "batch.csv"
    write_batch_csv(batch_id, out, session_factory)
    first = out.read_text()
    write_batch_csv(batch_id, out, session_factory)
    second = out.read_text()
    assert first == second
    assert list(tmp_path.glob(".*")) == []
