"""Fixtures for the expenses sandbox test suite."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sandbox.expenses.db import Base, Employee

EMPLOYEE_KEY = "employee-test-key"
APPROVER_KEY = "approver-test-key"
SELF_APPROVER_KEY = "self-approver-test-key"
OTHER_EMPLOYEE_KEY = "other-employee-test-key"

EMPLOYEE_EMAIL = "ada@example.com"
APPROVER_EMAIL = "dave@example.com"
SELF_APPROVER_EMAIL = "carol@example.com"
OTHER_EMPLOYEE_EMAIL = "erin@example.com"

EXPENSES_KEYS = ",".join(
    [
        f"employee:{EMPLOYEE_EMAIL}:{EMPLOYEE_KEY}",
        f"approver:{APPROVER_EMAIL}:{APPROVER_KEY}",
        f"approver:{SELF_APPROVER_EMAIL}:{SELF_APPROVER_KEY}",
        f"employee:{OTHER_EMPLOYEE_EMAIL}:{OTHER_EMPLOYEE_KEY}",
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
def seeded(db: Session) -> dict[str, Employee]:
    """Employees for both roles: ada only ever files claims; carol also
    holds an approver key, for the self-approval test; erin is a second,
    unrelated employee, for the ownership check on GET /claims/{id}."""
    ada = Employee(
        id=str(uuid.uuid4()), email=EMPLOYEE_EMAIL, manager_email=SELF_APPROVER_EMAIL, active=True
    )
    carol = Employee(id=str(uuid.uuid4()), email=SELF_APPROVER_EMAIL, active=True)
    erin = Employee(id=str(uuid.uuid4()), email=OTHER_EMPLOYEE_EMAIL, active=True)
    db.add_all([ada, carol, erin])
    db.commit()
    return {"ada": ada, "carol": carol, "erin": erin}


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    seeded: dict[str, Employee],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    monkeypatch.setenv("EXPENSES_KEYS", EXPENSES_KEYS)
    from sandbox.expenses import api

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
