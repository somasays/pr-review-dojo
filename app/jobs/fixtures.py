"""Small deterministic fixtures for local runs and tests."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pyspark.sql import SparkSession

from app.jobs.schemas import ORDER_LINES_SCHEMA, ORDERS_SCHEMA

STATUSES = ["paid", "paid", "shipped", "cancelled", "pending_payment", "delivered"]
SKUS = ["WIDGET", "GADGET", "GIZMO"]
PRODUCTS_DDL = (
    "sku string, name string, category string, unit_price decimal(12,2), effective_date string"
)
PRODUCT_ROWS = [
    ("WIDGET", "Widget", "home_office", Decimal("19.99"), "2026-01-01"),
    ("GADGET", "Gadget", "electronics", Decimal("120.00"), "2026-01-01"),
    ("GIZMO", "Gizmo", "electronics", Decimal("0.99"), "2026-01-01"),
]


def orders_rows(days: int = 3, per_day: int = 6, start: datetime | None = None) -> list[tuple]:
    start = start or datetime(2026, 8, 1, tzinfo=UTC)
    rows = []
    order_id = 1
    for d in range(days):
        day = start + timedelta(days=d)
        for i in range(per_day):
            rows.append(
                (
                    order_id,
                    (i % 3) + 1,
                    STATUSES[i % len(STATUSES)],
                    "USD",
                    Decimal(f"{10 * (i + 1)}.50"),
                    day + timedelta(hours=i),
                    day.strftime("%Y-%m-%d"),
                )
            )
            order_id += 1
    return rows


def write_orders_fixture(spark: SparkSession, root: str, days: int = 3) -> str:
    path = f"{root}/orders"
    df = spark.createDataFrame(orders_rows(days=days), ORDERS_SCHEMA)
    df.write.mode("overwrite").partitionBy("dt").parquet(path)
    return path


def order_lines_rows(days: int = 3, per_day: int = 6, start: datetime | None = None) -> list[tuple]:
    """One line per order, cycling through the catalog."""
    rows = []
    for order_id, _customer_id, status, _currency, total, _created_at, dt in orders_rows(
        days=days, per_day=per_day, start=start
    ):
        rows.append((order_id, SKUS[order_id % len(SKUS)], 1 + (order_id % 3), status, total, dt))
    return rows


def write_order_lines_fixture(spark: SparkSession, root: str, days: int = 3) -> str:
    path = f"{root}/order_lines"
    df = spark.createDataFrame(order_lines_rows(days=days), ORDER_LINES_SCHEMA)
    df.write.mode("overwrite").partitionBy("dt").parquet(path)
    return path


def write_products_fixture(spark: SparkSession, root: str, rows: list[tuple] | None = None) -> str:
    path = f"{root}/products"
    df = spark.createDataFrame(rows if rows is not None else PRODUCT_ROWS, PRODUCTS_DDL)
    df.write.mode("overwrite").parquet(path)
    return path


def write_events_fixture(source_dir: str, with_duplicate: bool = True) -> None:
    os.makedirs(source_dir, exist_ok=True)
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    events = [
        {
            "event_id": "e1",
            "order_id": 1,
            "customer_id": 1,
            "status": "pending_payment",
            "total": "10.50",
            "event_time": (base).isoformat(),
        },
        {
            "event_id": "e2",
            "order_id": 1,
            "customer_id": 1,
            "status": "paid",
            "total": "10.50",
            "event_time": (base + timedelta(minutes=1)).isoformat(),
        },
        {
            "event_id": "e3",
            "order_id": 2,
            "customer_id": 2,
            "status": "pending_payment",
            "total": "20.50",
            "event_time": (base + timedelta(minutes=2)).isoformat(),
        },
    ]
    if with_duplicate:
        events.append(dict(events[1]))
    with open(f"{source_dir}/events-0001.json", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
