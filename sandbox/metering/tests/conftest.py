"""Fixtures for the metering sandbox test suite."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.metering.db import Account, Base, Meter
from sandbox.metering.domain.tariff import Band, Tariff

READER_KEY = "reader-test-key"
CUSTOMER_KEY = "customer-test-key"
OTHER_CUSTOMER_KEY = "other-customer-test-key"

READER_EMAIL = "priya@example.com"
CUSTOMER_EMAIL = "ada@example.com"
OTHER_CUSTOMER_EMAIL = "erin@example.com"

METERING_KEYS = ",".join(
    [
        f"reader:{READER_EMAIL}:{READER_KEY}",
        f"customer:{CUSTOMER_EMAIL}:{CUSTOMER_KEY}",
        f"customer:{OTHER_CUSTOMER_EMAIL}:{OTHER_CUSTOMER_KEY}",
    ]
)

METER_SERIAL = "MTR-0001"
MAX_READING = Decimal("99999.999")

TARIFF = Tariff(
    standing_charge_per_day=Decimal("0.20"),
    bands=(
        Band(Decimal("100.000"), Decimal("0.30")),
        Band(Decimal("300.000"), Decimal("0.20")),
        Band(None, Decimal("0.10")),
    ),
)

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


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
def seeded(db: Session) -> dict[str, object]:
    """A customer account with one meter, and a second, unrelated account
    for cross-account access checks."""
    account = Account(email=CUSTOMER_EMAIL, active=True)
    other_account = Account(email=OTHER_CUSTOMER_EMAIL, active=True)
    db.add_all([account, other_account])
    db.flush()
    meter = Meter(account_id=account.id, serial=METER_SERIAL, max_reading=MAX_READING)
    db.add(meter)
    db.commit()
    return {"account": account, "other_account": other_account, "meter": meter}


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    seeded: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("METERING_KEYS", METERING_KEYS)
    from sandbox.metering import api

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
