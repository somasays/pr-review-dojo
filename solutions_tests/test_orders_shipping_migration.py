"""Hidden tests for exercise 10: the 0003 shipping-fields migration."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

VERSIONS = Path("app/db/alembic/versions")


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, str]:
    url = f"sqlite:///{tmp_path}/mig.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.services.config import get_settings

    get_settings.cache_clear()
    return Config("alembic.ini"), url


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    from app.services.config import get_settings

    yield
    get_settings.cache_clear()


def _revision_source(revision: str) -> str:
    script = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(revision)
    return Path(script.path).read_text()


def _insert_customer_and_order(url: str, currency: str = "EUR") -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO customers (email, name, region) "
                "VALUES ('ada@example.com', 'Ada', 'US-CA')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO orders (customer_id, idempotency_key, currency) "
                "VALUES (1, 'key-00000001', :currency)"
            ),
            {"currency": currency},
        )
    engine.dispose()


def test_downgrade_removes_only_the_new_columns(tmp_path, monkeypatch):
    """The rollback must not touch currency."""
    cfg, url = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")
    _insert_customer_and_order(url)

    command.downgrade(cfg, "0002")

    insp = inspect(create_engine(url))
    columns = {c["name"] for c in insp.get_columns("orders")}
    assert "shipped_at" not in columns
    assert "tracking_number" not in columns
    assert "currency" in columns
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT currency FROM orders")).scalar_one() == "EUR"
    engine.dispose()


def test_tracking_number_keeps_a_database_default(tmp_path, monkeypatch):
    """Writers that bypass the ORM must still be able to insert an order."""
    cfg, url = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")
    _insert_customer_and_order(url)

    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT tracking_number FROM orders")).scalar_one() == ""
    engine.dispose()
    insp = inspect(create_engine(url))
    tracking = next(c for c in insp.get_columns("orders") if c["name"] == "tracking_number")
    assert tracking["default"] is not None


def test_migration_does_not_backfill_data(tmp_path, monkeypatch):
    """A schema migration stays a metadata change; backfills run separately."""
    source = _revision_source("0003")
    assert "op.execute(" not in source
    assert "UPDATE" not in source.upper().replace("UPDATED_AT", "")

    cfg, url = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(url))
    shipped_at = next(c for c in insp.get_columns("orders") if c["name"] == "shipped_at")
    assert shipped_at["nullable"] is True


def test_shipped_at_is_declared_timezone_aware():
    source = _revision_source("0003")
    assert re.search(r'"shipped_at"\s*,\s*sa\.DateTime\(\s*timezone\s*=\s*True\s*\)', source), (
        "shipped_at must be declared as sa.DateTime(timezone=True)"
    )


def test_version_file_names_follow_the_pattern():
    names = sorted(p.name for p in VERSIONS.glob("*.py") if p.name != "__init__.py")
    for name in names:
        assert re.match(r"^\d{4}_[a-z0-9_]+\.py$", name), name


def test_docstring_headers_match_the_revision_variables():
    for path in sorted(VERSIONS.glob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text()
        revision = re.search(r'^revision: str = "([^"]*)"', source, re.M)
        down = re.search(r'^down_revision: Union\[str, None\] = (?:"([^"]*)"|None)', source, re.M)
        header_rev = re.search(r"^Revision ID:\s*(\S*)\s*$", source, re.M)
        header_down = re.search(r"^Revises:\s*(\S*)\s*$", source, re.M)
        assert revision is not None and header_rev is not None, path.name
        assert header_rev.group(1) == revision.group(1), path.name
        expected_down = down.group(1) if down is not None and down.group(1) else ""
        actual_down = header_down.group(1) if header_down is not None else ""
        assert actual_down == expected_down, path.name
