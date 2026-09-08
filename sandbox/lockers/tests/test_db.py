"""Tests for sandbox/lockers/db.py: ensure_naive_utc and unit_of_work."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from sandbox.lockers.db import Locker, ensure_naive_utc, unit_of_work


def test_ensure_naive_utc_rejects_aware_datetime() -> None:
    with pytest.raises(ValueError):
        ensure_naive_utc(datetime.now(UTC))
    ensure_naive_utc(datetime(2026, 9, 8, 9, 0))  # naive: no error


def test_unit_of_work_commits_on_success(session_factory: sessionmaker[Session]) -> None:
    with unit_of_work(session_factory) as session:
        session.add(Locker(site="Main St", active=True))

    with session_factory() as session:
        assert session.query(Locker).count() == 1


def test_unit_of_work_rolls_back_on_error(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(RuntimeError):
        with unit_of_work(session_factory) as session:
            session.add(Locker(site="Main St", active=True))
            raise RuntimeError("boom")

    with session_factory() as session:
        assert session.query(Locker).count() == 0
