"""Fixtures for the rooms sandbox test suite."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.rooms.db import Base, Room

TEST_API_KEY = "test-key"
HOLDER_EMAIL = "ada@example.com"


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
def room(db: Session) -> Room:
    room = Room(
        id=str(uuid.uuid4()),
        name="Falcon",
        capacity=8,
        rate_cents_per_hour=2000,
        active=True,
    )
    db.add(room)
    db.commit()
    return room


@pytest.fixture
def client(
    session_factory: sessionmaker[Session], room: Room, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("ROOMS_API_KEYS", f"{HOLDER_EMAIL}:{TEST_API_KEY}")
    from sandbox.rooms import api

    def _get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    with TestClient(api.app) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()
