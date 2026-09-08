"""CSV export for a payout batch.

write_batch_csv is idempotent: it always rewrites the whole file from the
database, writing to a temporary file in the same directory first and
renaming it into place, so a crash mid-write never leaves a truncated file
and re-running with the same batch_id reproduces the same output.
"""

from __future__ import annotations

import csv
import os
import tempfile
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from sandbox.expenses.db import Claim, ClaimLine, Employee

_CENTS = Decimal("0.01")


def write_batch_csv(batch_id: str, path: str | Path, session_factory: sessionmaker[Session]) -> int:
    """Write one row per paid claim in this batch to `path`.

    Columns are employee_email, claim_id, currency, total. Returns the
    number of rows written.
    """
    path = Path(path)
    session = session_factory()
    try:
        stmt = (
            select(Employee.email, Claim.id, Claim.currency, func.sum(ClaimLine.amount))
            .select_from(Claim)
            .join(Employee, Claim.employee_id == Employee.id)
            .join(ClaimLine, ClaimLine.claim_id == Claim.id)
            .where(Claim.paid_in_batch_id == batch_id, ClaimLine.outcome == "approved")
            .group_by(Claim.id, Employee.email, Claim.currency)
            .order_by(Claim.id)
        )
        rows = session.execute(stmt).all()
    finally:
        session.close()

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["employee_email", "claim_id", "currency", "total"])
            for email, claim_id, currency, total in rows:
                writer.writerow([email, claim_id, currency, Decimal(str(total)).quantize(_CENTS)])
        os.replace(tmp_name, path)
    except Exception:
        os.unlink(tmp_name)
        raise
    return len(rows)
