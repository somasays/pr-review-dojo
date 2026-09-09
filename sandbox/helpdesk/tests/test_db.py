from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from sandbox.helpdesk.db import Agent, unit_of_work


def test_unit_of_work_rollback(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(ValueError):
        with unit_of_work(session_factory) as session:
            session.add(Agent(email="ghost@example.com", active=True))
            session.flush()
            raise ValueError("boom")

    with session_factory() as check:
        stmt = select(Agent).where(Agent.email == "ghost@example.com")
        assert check.scalars(stmt).first() is None
