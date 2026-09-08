"""Tests for sandbox/expenses/db.py."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.expenses.db import Employee, unit_of_work


def test_unit_of_work_rolls_back_on_error(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(RuntimeError):
        with unit_of_work(session_factory) as session:
            session.add(Employee(id=str(uuid.uuid4()), email="temp@example.com", active=True))
            session.flush()
            raise RuntimeError("boom")

    with session_factory() as session:
        assert session.query(Employee).count() == 0
