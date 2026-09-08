"""Fixtures for the lockers sandbox test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.lockers.db import Base, Compartment, Locker

COURIER_KEY = "courier-test-key"


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
def locker(db: Session) -> Locker:
    """One locker with one compartment of each size."""
    loc = Locker(site="Main St", active=True)
    db.add(loc)
    db.flush()
    for size in ("S", "M", "L"):
        db.add(Compartment(locker_id=loc.id, size=size, occupied=False))
    db.commit()
    return loc


@pytest.fixture
def client(
    session_factory: sessionmaker[Session], locker: Locker, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("LOCKERS_COURIER_KEYS", COURIER_KEY)
    from sandbox.lockers import api

    def _get_session_factory() -> sessionmaker[Session]:
        return session_factory

    def _get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    api.app.dependency_overrides[api.get_session_factory_dep] = _get_session_factory
    api.app.dependency_overrides[api.get_db] = _get_db
    with TestClient(api.app) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()
