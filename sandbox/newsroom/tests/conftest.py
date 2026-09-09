"""Fixtures for the newsroom sandbox test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.newsroom.db import Base, Section

EDITOR_EMAIL = "edie@example.com"
EDITOR_KEY = "editor-test-key"
READER_EMAIL = "randy@example.com"
READER_KEY = "reader-test-key"

NEWSROOM_KEYS = ",".join(
    [
        f"editor:{EDITOR_EMAIL}:{EDITOR_KEY}",
        f"reader:{READER_EMAIL}:{READER_KEY}",
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
def section(db: Session) -> Section:
    section = Section(name="Front Page", slot_count=3)
    db.add(section)
    db.commit()
    return section


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    section: Section,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("NEWSROOM_KEYS", NEWSROOM_KEYS)
    from sandbox.newsroom import api

    def _get_session_factory() -> sessionmaker[Session]:
        return session_factory

    api.app.dependency_overrides[api.get_session_factory] = _get_session_factory
    # The refresher is built once in create_app() against the process-wide
    # engine; point it at this test's engine so GET /home reflects this
    # test's data when a test calls refresher.run_once() directly.
    api.app.state.refresher.session_factory = session_factory
    with TestClient(api.app) as test_client:
        yield test_client
    api.app.dependency_overrides.clear()
