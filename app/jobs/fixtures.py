"""Small deterministic fixtures for local runs and tests."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pyspark.sql import SparkSession

from app.jobs.schemas import ORDERS_SCHEMA

STATUSES = ["paid", "paid", "shipped", "cancelled", "pending_payment", "delivered"]


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


def write_events_fixture(
    source_dir: str, with_duplicate: bool = True, with_malformed: bool = False
) -> None:
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
    lines = [json.dumps(e) for e in events]
    if with_malformed:
        # A producer that died mid-line leaves a truncated record behind.
        lines.append('{"event_id": "e4", "order_id": 4, "status": "shi')
    with open(f"{source_dir}/events-0001.json", "w") as f:
        for line in lines:
            f.write(line + "\n")
