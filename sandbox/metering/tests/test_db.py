"""db.py: ensure_aware_utc and session_scope's transaction boundary."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sandbox.metering.db import Account, ensure_aware_utc, session_scope


def test_ensure_aware_utc_rejects_naive_but_accepts_aware() -> None:
    with pytest.raises(ValueError):
        ensure_aware_utc(datetime(2026, 1, 1))
    ensure_aware_utc(datetime(2026, 1, 1, tzinfo=UTC))  # does not raise


def test_session_scope_rolls_back_on_error(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(RuntimeError):
        with session_scope(session_factory) as session:
            session.add(Account(email="doomed@example.com", active=True))
            session.flush()
            raise RuntimeError("boom")

    with session_scope(session_factory) as verify:
        row = verify.scalars(select(Account).where(Account.email == "doomed@example.com")).first()
        assert row is None
