"""Tests for the engine, session factory, and unit_of_work transaction
boundary."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from sandbox.timesheets.db import Worker, unit_of_work


def test_create_all_creates_every_table_and_unit_of_work_commits_and_rolls_back(
    engine, session_factory: sessionmaker[Session]
) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"workers", "timesheets", "shifts"} <= tables

    with unit_of_work(session_factory) as session:
        session.add(Worker(email="a@example.com", timezone="UTC", hourly_rate=Decimal("10.0000")))

    with session_factory() as check:
        assert check.query(Worker).count() == 1

    with pytest.raises(RuntimeError):
        with unit_of_work(session_factory) as session:
            session.add(
                Worker(email="b@example.com", timezone="UTC", hourly_rate=Decimal("10.0000"))
            )
            session.flush()
            raise RuntimeError("boom")

    with session_factory() as check:
        # The failed unit of work left no trace: still just the one worker.
        assert check.query(Worker).count() == 1
