"""Shared fixtures for tests/ and solutions_tests/."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Local dev on macOS: Homebrew's JDK is keg-only and not on PATH.
_BREW_JDK = "/opt/homebrew/opt/openjdk@17"
if "JAVA_HOME" not in os.environ and os.path.isdir(_BREW_JDK):
    os.environ["JAVA_HOME"] = _BREW_JDK
    os.environ["PATH"] = f"{_BREW_JDK}/bin:{os.environ['PATH']}"
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

ADMIN_KEY = "admin-test-key"
CUSTOMER_KEY = "customer-test-key"


@pytest.fixture(scope="session", autouse=True)
def _settings_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    from app.services.config import get_settings

    os.environ["APP_ENV"] = "test"
    os.environ["ADMIN_API_KEYS"] = ADMIN_KEY
    os.environ["NOTIFY_RETRIES"] = "3"
    os.environ["DATABASE_URL"] = "sqlite://"
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine():
    from app.db.models import Base

    # StaticPool: one shared connection so the in-memory database is visible to every session.
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    @event.listens_for(eng, "connect")
    def _fk(conn, _rec):  # noqa: ANN001
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    s = session_factory()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def seeded(db: Session) -> dict[str, object]:
    """One customer with an API key, three products."""
    from app.api.deps import hash_api_key
    from app.db.models import Customer, Product

    customer = Customer(
        email="ada@example.com",
        name="Ada",
        region="US-CA",
        api_key_hash=hash_api_key(CUSTOMER_KEY),
    )
    products = [
        Product(sku="WIDGET", name="Widget", unit_price=Decimal("19.99"), stock=100),
        Product(sku="GADGET", name="Gadget", unit_price=Decimal("120.00"), stock=5),
        Product(sku="GIZMO", name="Gizmo", unit_price=Decimal("0.99"), stock=0),
    ]
    db.add(customer)
    db.add_all(products)
    db.commit()
    return {"customer": customer, "products": {p.sku: p for p in products}}


@pytest.fixture
def client(session_factory: sessionmaker[Session], seeded) -> Iterator[TestClient]:
    from app.api.deps import get_db
    from app.api.main import create_app

    app = create_app()

    def _get_db() -> Iterator[Session]:
        s = session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def spark():
    from app.jobs.spark_session import get_spark

    s = get_spark("dojo-tests")
    yield s
    s.stop()


@pytest.fixture
def lake(spark, tmp_path: Path) -> str:
    from app.jobs.fixtures import write_customers_fixture, write_orders_fixture

    root = str(tmp_path / "lake")
    write_orders_fixture(spark, root, days=3)
    write_customers_fixture(spark, root)
    return root
