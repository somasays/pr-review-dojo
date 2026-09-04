from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_migrations_upgrade_and_downgrade(tmp_path: Path, monkeypatch):
    url = f"sqlite:///{tmp_path}/mig.db"
    monkeypatch.setenv("DATABASE_URL", url)
    from app.services.config import get_settings

    get_settings.cache_clear()
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(url))
    assert {"customers", "products", "orders", "order_items"} <= set(insp.get_table_names())
    assert "ix_orders_customer_created" in {i["name"] for i in insp.get_indexes("orders")}
    command.downgrade(cfg, "base")
    assert "orders" not in set(inspect(create_engine(url)).get_table_names())
    get_settings.cache_clear()
