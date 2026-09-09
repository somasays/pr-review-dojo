"""Fixtures for the timesheets sandbox test suite."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.timesheets.db import Base, Worker
from sandbox.timesheets.domain.pay import Rules

WORKER_KEY = "worker-test-key"
NY_WORKER_KEY = "ny-worker-test-key"
MANAGER_KEY = "manager-test-key"
SELF_MANAGER_KEY = "self-manager-test-key"

WORKER_EMAIL = "priya@example.com"
NY_WORKER_EMAIL = "sam@example.com"
MANAGER_EMAIL = "dana@example.com"
SELF_MANAGER_EMAIL = "kai@example.com"

TIMESHEETS_KEYS = ",".join(
    [
        f"worker:{WORKER_EMAIL}:{WORKER_KEY}",
        f"worker:{NY_WORKER_EMAIL}:{NY_WORKER_KEY}",
        f"manager:{MANAGER_EMAIL}:{MANAGER_KEY}",
        f"manager:{SELF_MANAGER_EMAIL}:{SELF_MANAGER_KEY}",
    ]
)


@pytest.fixture
def rules() -> Rules:
    return Rules(
        daily_overtime_after_minutes=8 * 60,
        weekly_overtime_after_minutes=40 * 60,
        overtime_multiplier=Decimal("1.5"),
        night_start_hour=22,
        night_end_hour=6,
        night_differential=Decimal("0.10"),
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
def seeded(db: Session) -> dict[str, Worker]:
    """Workers in two timezones, plus a manager who also holds a worker
    record (for the self-approval test)."""
    priya = Worker(
        email=WORKER_EMAIL,
        timezone="America/Los_Angeles",
        hourly_rate=Decimal("22.5000"),
        active=True,
    )
    sam = Worker(
        email=NY_WORKER_EMAIL,
        timezone="America/New_York",
        hourly_rate=Decimal("30.0000"),
        active=True,
    )
    kai = Worker(
        email=SELF_MANAGER_EMAIL,
        timezone="America/New_York",
        hourly_rate=Decimal("40.0000"),
        active=True,
    )
    db.add_all([priya, sam, kai])
    db.commit()
    return {"priya": priya, "sam": sam, "kai": kai}


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    seeded: dict[str, Worker],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("TIMESHEETS_KEYS", TIMESHEETS_KEYS)
    from sandbox.timesheets import api

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
