"""Fixtures for the adserver sandbox test suite."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.adserver.db import Advertiser, Base, Campaign
from sandbox.adserver.domain.pacing import CampaignStatus

OPS_EMAIL = "ops@example.com"
OPS_KEY = "ops-test-key"
ADVERTISER_EMAIL = "adele@example.com"
ADVERTISER_KEY = "advertiser-test-key"
OTHER_ADVERTISER_EMAIL = "otto@example.com"
OTHER_ADVERTISER_KEY = "other-advertiser-test-key"

ADSERVER_KEYS = ",".join(
    [
        f"ops:{OPS_EMAIL}:{OPS_KEY}",
        f"advertiser:{ADVERTISER_EMAIL}:{ADVERTISER_KEY}",
        f"advertiser:{OTHER_ADVERTISER_EMAIL}:{OTHER_ADVERTISER_KEY}",
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
def advertiser(db: Session) -> Advertiser:
    advertiser = Advertiser(email=ADVERTISER_EMAIL)
    db.add(advertiser)
    db.commit()
    return advertiser


@pytest.fixture
def other_advertiser(db: Session) -> Advertiser:
    other = Advertiser(email=OTHER_ADVERTISER_EMAIL)
    db.add(other)
    db.commit()
    return other


@pytest.fixture
def campaign(db: Session, advertiser: Advertiser) -> Campaign:
    campaign = Campaign(
        advertiser_id=advertiser.id,
        name="Fall Sale",
        daily_budget=Decimal("10.00"),
        cpm=Decimal("2.00"),
        status=CampaignStatus.ACTIVE.value,
    )
    db.add(campaign)
    db.commit()
    return campaign


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("ADSERVER_KEYS", ADSERVER_KEYS)
    from sandbox.adserver import api

    def _get_session_factory() -> sessionmaker[Session]:
        return session_factory

    api.app.dependency_overrides[api.get_session_factory] = _get_session_factory
    # The flusher is built once in create_app() against the process-wide
    # engine; point it at this test's engine so a direct call to
    # run_once() would see this test's data.
    api.app.state.flusher.session_factory = session_factory
    # The tracker is a process-wide singleton too; start each test with no
    # leftover impression counts from a previous test's campaign ids.
    api.app.state.tracker.counts.clear()
    with TestClient(api.app) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()
