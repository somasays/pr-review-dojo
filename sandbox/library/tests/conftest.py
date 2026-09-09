"""Fixtures for the library sandbox test suite."""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.library.db import Base, Item, Patron

LIBRARIAN_KEY = "librarian-test-key"
PATRON_KEY = "patron-test-key"
OTHER_PATRON_KEY = "other-patron-test-key"
LIBRARIAN_EMAIL = "carol@example.com"
PATRON_EMAIL = "alice@example.com"
OTHER_PATRON_EMAIL = "bob@example.com"


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
def seeded(db: Session) -> dict[str, Patron | Item]:
    """Two patrons (one blockable, one not) and two items."""
    alice = Patron(email=PATRON_EMAIL, blocked=False)
    bob = Patron(email=OTHER_PATRON_EMAIL, blocked=False)
    db.add_all([alice, bob])
    db.flush()
    book = Item(title="Book One", copies=1, replacement_cost=Decimal("20.00"))
    other_book = Item(title="Book Two", copies=2, replacement_cost=Decimal("15.00"))
    db.add_all([book, other_book])
    db.flush()
    db.commit()
    return {"alice": alice, "bob": bob, "book": book, "other_book": other_book}


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    seeded: dict[str, Patron | Item],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv(
        "LIBRARY_KEYS",
        f"librarian:{LIBRARIAN_EMAIL}:{LIBRARIAN_KEY},"
        f"patron:{PATRON_EMAIL}:{PATRON_KEY},"
        f"patron:{OTHER_PATRON_EMAIL}:{OTHER_PATRON_KEY}",
    )
    from sandbox.library import api

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
