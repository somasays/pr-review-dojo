"""Fixtures for the parking sandbox test suite."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.parking.db import Base, Garage
from sandbox.parking.domain.pricing import RateCard

ATTENDANT_KEY = "attendant-test-key"
PARKING_KEYS = ATTENDANT_KEY

GARAGE_CAPACITY = 2
EPOCH = datetime(2026, 1, 1, tzinfo=UTC)

CARD = RateCard(
    grace_minutes=15,
    first_hour_cents=400,
    extra_hour_cents=200,
    daily_cap_cents=2000,
    lost_ticket_cents=5000,
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
def garage(db: Session) -> Garage:
    garage = Garage(name="Downtown", capacity=GARAGE_CAPACITY)
    db.add(garage)
    db.commit()
    return garage


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    garage: Garage,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("PARKING_KEYS", PARKING_KEYS)
    from sandbox.parking import api

    def _get_db() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    api.app.dependency_overrides[api.get_db] = _get_db
    with TestClient(api.app) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()
