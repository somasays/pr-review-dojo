"""Hidden tests for exercise 26."""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

from pyspark.sql import DataFrame

from app.domain.dates import DateRange
from app.jobs import daily_orders as job

DAY = DateRange.single(date(2026, 8, 1))
THREE_DAYS = DateRange(date(2026, 8, 1), date(2026, 8, 3))

SHIPPED_TESTS = (
    Path(__file__).resolve().parent.parent / "tests" / "test_daily_orders.py"
).read_text()


def test_backfill_does_not_wipe_earlier_chunks_from_the_table(spark, lake):
    """A static partition overwrite mid-backfill must not delete other days."""
    paths = job.LakePaths(lake)
    job.run_backfill(spark, paths, THREE_DAYS, chunk_days=1)
    written = spark.read.parquet(paths.daily_customer_orders)
    assert set(r.dt for r in written.select("dt").distinct().collect()) == {
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    }
    assert spark.conf.get("spark.sql.sources.partitionOverwriteMode") == "dynamic"


def test_extract_is_written_without_pulling_rows_to_the_driver(spark, lake, monkeypatch):
    paths = job.LakePaths(lake)

    def boom(self, *args, **kwargs):
        raise AssertionError("the whole aggregate was pulled to the driver")

    monkeypatch.setattr(DataFrame, "collect", boom)
    monkeypatch.setattr(DataFrame, "toPandas", boom, raising=False)
    job.run(spark, paths, DAY)
    monkeypatch.undo()
    extract = spark.read.option("header", True).csv(f"{paths.extracts}/2026-08-01_2026-08-01.csv")
    assert extract.count() == 3
    assert "paid_total" in extract.columns


def test_read_orders_does_not_cache_the_whole_table(spark, lake):
    paths = job.LakePaths(lake)
    df = job.read_orders(spark, paths, DAY)
    plan = df._jdf.queryExecution().executedPlan().toString()
    assert "InMemoryTableScan" not in plan
    assert "PartitionFilters: [isnotnull(dt" in plan


def test_dimension_join_can_broadcast(spark, lake):
    assert spark.conf.get("spark.sql.autoBroadcastJoinThreshold") != "-1"
    assert spark.conf.get("spark.sql.adaptive.enabled") == "true"
    paths = job.LakePaths(lake)
    orders = job.read_orders(spark, paths, DAY)
    labeled = job.with_customer_region(orders, job.read_customers(spark, paths))
    plan = labeled._jdf.queryExecution().executedPlan().toString()
    assert "SortMergeJoin" not in plan


def test_backfill_keeps_utc_day_boundaries(spark, lake):
    paths = job.LakePaths(lake)
    job.run_backfill(spark, paths, THREE_DAYS, chunk_days=2)
    written = spark.read.parquet(paths.daily_customer_orders)
    counts = {r.dt: r["count"] for r in written.groupBy("dt").count().collect()}
    assert counts == {"2026-08-01": 3, "2026-08-02": 3, "2026-08-03": 3}


def test_salt_buckets_match_the_replicated_dimension(spark, lake):
    """The order side and the dimension side must be spread over the same bucket count."""
    paths = job.LakePaths(lake)
    original = spark.conf.get("spark.sql.shuffle.partitions")
    spark.conf.set("spark.sql.shuffle.partitions", "32")
    try:
        daily = job.run(spark, paths, DAY).collect()
    finally:
        spark.conf.set("spark.sql.shuffle.partitions", original)
    assert sorted((r.customer_id, r.order_count, r.paid_total) for r in daily) == [
        (1, 2, Decimal("10.50")),
        (2, 2, Decimal("20.50")),
        (3, 2, Decimal("91.00")),
    ]


def test_run_takes_no_boolean_backfill_flag():
    """The single-day and backfill paths are separate functions, not a bool switch."""
    run_params = inspect.signature(job.run).parameters
    assert not any(p.annotation is bool for p in run_params.values())
    assert callable(job.run_for_backfill_chunk)
    backfill_params = inspect.signature(job.run_for_backfill_chunk).parameters
    assert not any(p.annotation is bool for p in backfill_params.values())


def test_default_backfill_range_reuses_date_range_helper():
    """No hand-rolled date.today()/timedelta math when DateRange.last_n_days exists."""
    source = inspect.getsource(job.default_backfill_range)
    assert "last_n_days" in source
    assert "timedelta" not in source


def test_default_backfill_range_takes_a_today_parameter():
    """A default-window helper must accept the clock so a test can pin it."""
    params = inspect.signature(job.default_backfill_range).parameters
    assert "today" in params
    fixed = date(2026, 8, 15)
    assert job.default_backfill_range(today=fixed) == DateRange(
        date(2026, 7, 16), date(2026, 8, 14)
    )
    assert job.default_backfill_range(today=fixed) == job.default_backfill_range(today=fixed)


def test_write_csv_extract_is_directly_tested():
    """A new public function must be covered by its own test, not only indirectly."""
    assert "write_csv_extract" in SHIPPED_TESTS
