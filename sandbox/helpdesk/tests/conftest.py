"""Fixtures for the helpdesk sandbox test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.helpdesk.db import Agent, Base

AGENT_EMAIL = "alice@example.com"
AGENT_KEY = "agent-test-key"
OTHER_AGENT_EMAIL = "bob@example.com"
OTHER_AGENT_KEY = "other-agent-test-key"
LEAD_EMAIL = "dana@example.com"
LEAD_KEY = "lead-test-key"

HELPDESK_KEYS = ",".join(
    [
        f"agent:{AGENT_EMAIL}:{AGENT_KEY}",
        f"agent:{OTHER_AGENT_EMAIL}:{OTHER_AGENT_KEY}",
        f"lead:{LEAD_EMAIL}:{LEAD_KEY}",
    ]
)


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def seeded(db: Session) -> dict[str, Agent]:
    alice = Agent(email=AGENT_EMAIL, active=True)
    bob = Agent(email=OTHER_AGENT_EMAIL, active=True)
    db.add_all([alice, bob])
    db.commit()
    return {"alice": alice, "bob": bob}


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    seeded: dict[str, Agent],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("HELPDESK_KEYS", HELPDESK_KEYS)
    from sandbox.helpdesk import api

    def _get_session_factory() -> sessionmaker[Session]:
        return session_factory

    api.app.dependency_overrides[api.get_session_factory] = _get_session_factory
    with TestClient(api.app) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()
