"""Hidden tests for exercise 18."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, text

VERSIONS = Path("app/db/alembic/versions")


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from app.services.config import get_settings

    yield
    get_settings.cache_clear()


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, Engine]:
    url = f"sqlite:///{tmp_path}/mig.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.services.config import get_settings

    get_settings.cache_clear()
    return Config("alembic.ini"), create_engine(url)


def _revision_source(revision: str) -> str:
    for path in sorted(VERSIONS.glob("*.py")):
        source = path.read_text()
        if re.search(rf'^revision(: str)? = "{revision}"', source, re.MULTILINE):
            return source
    raise AssertionError(f"no migration declares revision {revision}")


def _columns(engine: Engine, table: str) -> dict[str, dict[str, object]]:
    return {c["name"]: c for c in inspect(engine).get_columns(table)}


def test_upgrade_keeps_existing_customer_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MG-01: the new columns must not break a table that already has rows."""
    cfg, engine = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "0002")
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO customers (email, name, region) VALUES (:e, :n, :r)"),
            {"e": "ada@example.com", "n": "Ada Lovelace", "r": "US-CA"},
        )

    command.upgrade(cfg, "head")

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT first_name, last_name FROM customers WHERE email = 'ada@example.com'")
        ).one()
    assert row.first_name == ""
    assert row.last_name == ""
    columns = _columns(engine, "customers")
    assert columns["last_name"]["default"] is not None
    assert columns["first_name"]["default"] is not None


def test_customers_index_is_built_without_blocking_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MG-02: the index on the live customers table is built concurrently."""
    source = _revision_source("0003")
    assert "postgresql_concurrently=True" in source
    assert "autocommit_block(" in source

    cfg, engine = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")
    names = {i["name"] for i in inspect(engine).get_indexes("customers")}
    assert any(name is not None and "last_name" in name for name in names)


def test_downgrade_removes_the_new_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MG-07: rolling back the release must leave 0002 behind."""
    cfg, engine = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")

    command.downgrade(cfg, "0002")

    columns = _columns(engine, "customers")
    assert "first_name" not in columns
    assert "last_name" not in columns
    assert "customer_name_backfill_log" not in set(inspect(engine).get_table_names())
    command.upgrade(cfg, "head")
    assert "first_name" in _columns(engine, "customers")


def test_backfill_log_foreign_key_is_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MG-09: the foreign key column carries its own index."""
    cfg, engine = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")
    indexes = inspect(engine).get_indexes("customer_name_backfill_log")
    assert any(i["column_names"][:1] == ["customer_id"] for i in indexes)


def test_no_index_drift_against_the_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MG-12: autogenerate must not want to rebuild any index."""
    from app.db.models import Base

    cfg, engine = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    index_diffs = [d for d in diffs if isinstance(d, tuple) and "index" in str(d[0])]
    assert index_diffs == []


def test_split_at_is_timezone_aware() -> None:
    """MG-14: timestamps are stored as aware UTC, like every other column."""
    source = _revision_source("0003")
    match = re.search(r'"split_at",\s*sa\.DateTime\(([^)]*)\)', source)
    assert match is not None, "0003 does not declare split_at"
    assert "timezone=True" in match.group(1)


def test_version_files_are_named_and_labelled_consistently() -> None:
    """MG-16 and MG-17: file names sort by revision and headers tell the truth."""
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        assert re.match(r"^\d{4}_[a-z0-9_]+\.py$", path.name), path.name
        source = path.read_text()
        revision = re.search(r'^revision(?:: str)? = "([^"]+)"', source, re.MULTILINE)
        down = re.search(r'^down_revision(?:.*)? = (None|"[^"]+")', source, re.MULTILINE)
        assert revision is not None and down is not None
        header_id = re.search(r"^Revision ID: (\S+)$", source, re.MULTILINE)
        header_down = re.search(r"^Revises:\s*(\S*)$", source, re.MULTILINE)
        assert header_id is not None and header_down is not None
        assert header_id.group(1) == revision.group(1), path.name
        expected_down = "" if down.group(1) == "None" else down.group(1).strip('"')
        assert header_down.group(1) == expected_down, path.name
