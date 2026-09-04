"""Hidden tests for exercise 04."""

import ast
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

from app.db.models import Customer, CustomerAddress
from app.db.repositories import CustomerRepository


def _add(db, name: str, region: str) -> Customer:
    row = Customer(email=f"{name.lower()}@example.com", name=name, region=region)
    db.add(row)
    db.commit()
    return row


def test_empty_region_filter_matches_every_region(db, seeded):
    """TR-01: the shipped test never covers 'no region checkbox ticked'."""
    repo = CustomerRepository(db)
    _add(db, "Nina", "US-CA")
    _add(db, "Noel", "US-NY")

    rows = repo.search("N", [])

    assert sorted(r.name for r in rows) == ["Nina", "Noel"]
    assert repo.search_count("N", []) == 2


def test_migration_indexes_the_customer_search_columns(tmp_path: Path, monkeypatch):
    """SA-08: the new query needs an index that the migrations actually create."""
    url = f"sqlite:///{tmp_path}/mig.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.services.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "head")
        indexes = inspect(create_engine(url)).get_indexes("customers")
        assert any(i["column_names"][:1] == ["region"] for i in indexes), indexes
    finally:
        get_settings.cache_clear()


def test_second_default_address_race_is_rejected(db, seeded):
    """SA-05: nothing stops two rows from being the default at once without a constraint."""
    customer_id = seeded["customer"].id
    db.add(CustomerAddress(customer_id=customer_id, line1="1 Main St", is_default=True))
    db.add(CustomerAddress(customer_id=customer_id, line1="2 Oak Ave", is_default=True))
    with pytest.raises(IntegrityError):
        db.commit()


def test_import_many_survives_a_duplicate_in_the_middle(db, seeded):
    """SA-11: a caught IntegrityError must not poison the rest of the batch."""
    repo = CustomerRepository(db)
    rows = [
        Customer(email="ada@example.com", name="Ada dup", region="US-CA"),
        Customer(email="nina@example.com", name="Nina", region="US-CA"),
        Customer(email="noel@example.com", name="Noel", region="US-NY"),
    ]

    skipped = repo.import_many(rows)

    assert skipped == ["ada@example.com"]
    assert repo.by_email("nina@example.com") is not None
    assert repo.by_email("noel@example.com") is not None


def test_address_endpoint_does_not_build_its_own_query():
    """DS-05: the router should call the repository, not compose SQLAlchemy itself."""
    source = Path("app/api/routers/customers.py").read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy":
            pytest.fail("router imports sqlalchemy directly")
        if isinstance(node, ast.Import) and any(a.name == "sqlalchemy" for a in node.names):
            pytest.fail("router imports sqlalchemy directly")
        if isinstance(node, ast.Attribute) and node.attr in {"execute", "scalars"}:
            pytest.fail(f"router calls session.{node.attr} directly")


def test_search_and_search_count_share_one_predicate_helper():
    """Refactor (DS-20): search and search_count should not build the predicate twice."""
    source = Path("app/db/repositories.py").read_text()
    tree = ast.parse(source)

    calls_helper: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {"search", "search_count"}:
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr.startswith("_")
                    and isinstance(inner.func.value, ast.Name)
                ):
                    calls_helper.add(node.name)

    assert calls_helper == {"search", "search_count"}, calls_helper
