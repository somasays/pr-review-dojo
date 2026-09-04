"""Backfill ``customers.first_name`` and ``customers.last_name``.

The 0003 migration only adds the columns. This script fills them from the
existing display name in batches so it can be stopped and restarted, and it
writes one audit row per customer it touches:

    uv run python -m app.db.backfill_customer_names --batch-size 500
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Customer, CustomerNameBackfill
from app.db.session import get_session_factory
from app.domain.names import split_full_name

log = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500


def pending_customers(session: Session, batch_size: int) -> list[Customer]:
    """Customers that have no audit row yet, oldest first."""
    done = select(CustomerNameBackfill.customer_id)
    stmt = select(Customer).where(Customer.id.not_in(done)).order_by(Customer.id).limit(batch_size)
    return list(session.scalars(stmt).all())


def backfill_batch(session: Session, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Split one batch of display names. Returns the number of rows touched.

    The caller owns the transaction.
    """
    batch = pending_customers(session, batch_size)
    for customer in batch:
        first_name, last_name = split_full_name(customer.name)
        customer.first_name = first_name
        customer.last_name = last_name
        session.add(CustomerNameBackfill(customer_id=customer.id, original_name=customer.name))
    session.flush()
    return len(batch)


def run(factory: sessionmaker[Session], batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """Loop over batches until nothing is pending. Returns the total touched."""
    total = 0
    while True:
        with factory() as session:
            touched = backfill_batch(session, batch_size)
            session.commit()
        if touched == 0:
            return total
        total += touched
        log.info("split %d customers, %d so far", touched, total)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill split customer names")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    total = run(get_session_factory(), args.batch_size)
    log.info("backfill finished, %d customers split", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
