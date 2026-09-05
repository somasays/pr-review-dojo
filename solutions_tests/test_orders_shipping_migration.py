"""Hidden tests for exercise 10: the shipping-fields migrations."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
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
    from alembic.script import ScriptDirectory

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


def test_tracking_number_keeps_a_database_default(tmp_path, monkeypatch):
    """MG-10: writers that bypass the ORM must still be able to insert an order."""
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


def test_shipped_at_is_declared_timezone_aware():
    """MG-14: shipped_at must match the model's DateTime(timezone=True)."""
    source = _revision_source("0003")
    assert re.search(r'"shipped_at"\s*,\s*sa\.DateTime\(\s*timezone\s*=\s*True\s*\)', source), (
        "shipped_at must be declared as sa.DateTime(timezone=True)"
    )


def test_tracking_id_is_added_without_dropping_tracking_number(tmp_path, monkeypatch):
    """MG-04: expand-and-contract. Old pods reading tracking_number must not break.

    Renaming a column in one step, in the same deploy that changes the model,
    breaks every pod still running the previous release the moment this
    revision runs. The fix keeps tracking_number in place and only adds the
    new column; a later revision drops tracking_number once nothing reads it.
    """
    cfg, url = _config(tmp_path, monkeypatch)
    command.upgrade(cfg, "head")
    _insert_customer_and_order(url)

    engine = create_engine(url)
    with engine.begin() as conn:
        # A pod still on the previous release writes the old column name.
        conn.execute(text("UPDATE orders SET tracking_number = 'OLD-RELEASE' WHERE id = 1"))
        assert (
            conn.execute(text("SELECT tracking_number FROM orders")).scalar_one() == "OLD-RELEASE"
        )
    engine.dispose()

    insp = inspect(create_engine(url))
    columns = {c["name"] for c in insp.get_columns("orders")}
    assert "tracking_number" in columns
    assert "tracking_id" in columns
    tracking_id = next(c for c in insp.get_columns("orders") if c["name"] == "tracking_id")
    assert tracking_id["nullable"] is False
    assert tracking_id["default"] is not None
