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


STATUS_FLOW = ["pending_payment", "paid", "shipped", "delivered"]


def status_change_events(
    base: datetime, hours: int = 4, per_hour: int = 3
) -> list[dict[str, object]]:
    """Status change events spread over consecutive hourly windows."""
    events: list[dict[str, object]] = []
    n = 0
    for h in range(hours):
        for i in range(per_hour):
            n += 1
            events.append(
                {
                    "event_id": f"s{n}",
                    "order_id": 100 + n,
                    "customer_id": (n % 3) + 1,
                    "status": STATUS_FLOW[i % len(STATUS_FLOW)],
                    "total": "42.00",
                    "event_time": (base + timedelta(hours=h, minutes=i)).isoformat(),
                }
            )
    return events


def write_status_change_fixture(source_dir: str, hours: int = 4, per_hour: int = 3) -> str:
    """Write one events file that spans several hourly windows."""
    os.makedirs(source_dir, exist_ok=True)
    path = f"{source_dir}/events-9001.json"
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    with open(path, "w") as f:
        for event in status_change_events(base, hours=hours, per_hour=per_hour):
            f.write(json.dumps(event) + "\n")
    return path
