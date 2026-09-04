"""Hidden tests for exercise 06."""

import glob
import os
from datetime import date
from decimal import Decimal

import pytest
from pyspark.sql import DataFrame

from app.domain.dates import DateRange
from app.jobs.daily_orders import LakePaths
from app.jobs.daily_orders import run as run_daily
from app.jobs.fixtures import write_customers_fixture
from app.jobs.schemas import CUSTOMERS_SCHEMA, DAILY_CUSTOMER_SCHEMA, WEEKLY_CUSTOMER_SCHEMA
from app.jobs.weekly_summary import run, weekly_summary

RANGE = DateRange(date(2026, 8, 1), date(2026, 8, 3))
MONDAY = DateRange.single(date(2026, 8, 3))


def _warehouse(spark, lake: str, customers: list[tuple] | None = None) -> LakePaths:
    paths = LakePaths(lake)
    run_daily(spark, paths, RANGE)
    if customers is None:
        write_customers_fixture(spark, lake)
    else:
        spark.createDataFrame(customers, CUSTOMERS_SCHEMA).write.mode("overwrite").parquet(
            f"{lake}/customers"
        )
    return paths


def _weekly_table(spark, lake: str):
    return spark.read.parquet(f"{lake}/weekly_customer_summary")


def _daily_scan_line(df: DataFrame) -> str:
    """The physical FileScan node that reads daily_customer_orders."""
    plan = df._jdf.queryExecution().executedPlan().toString()
    scans = [line for line in plan.splitlines() if "FileScan" in line and "paid_total" in line]
    assert len(scans) == 1, plan
    return scans[0]


def test_weekly_summary_prunes_the_daily_partitions(spark, lake):
    """The daily table must be pruned on dt, not scanned in full."""
    paths = _warehouse(spark, lake)
    scan = _daily_scan_line(weekly_summary(spark, paths, MONDAY))
    assert "PartitionFilters: []" not in scan, scan
    assert "PartitionFilters: [dt#" in scan, scan


def test_customer_missing_from_the_dimension_is_kept(spark, lake):
    """A customer with no dimension row still has to appear in the report."""
    paths = _warehouse(spark, lake, customers=[(1, "One", "US-CA"), (2, "Two", "US-NY")])
    rows = {r.customer_id: r for r in weekly_summary(spark, paths, MONDAY).collect()}
    assert set(rows) == {1, 2, 3}
    assert rows[3].region == "unknown"
    assert rows[3].paid_total == Decimal("91.00")


def test_weekly_write_produces_one_file_per_week(spark, tmp_path):
    """The writer must lay out files by week_start, not by customer."""
    root = str(tmp_path / "lake")
    paths = LakePaths(root)
    daily = [(c, 2, Decimal("10.00"), 0, "2026-08-03") for c in range(1, 13)]
    spark.createDataFrame(daily, DAILY_CUSTOMER_SCHEMA).write.mode("overwrite").partitionBy(
        "dt"
    ).parquet(paths.daily_customer_orders)
    customers = [(c, f"C{c}", "US-CA") for c in range(1, 13)]
    spark.createDataFrame(customers, CUSTOMERS_SCHEMA).write.mode("overwrite").parquet(
        f"{root}/customers"
    )
    shuffle_partitions = spark.conf.get("spark.sql.shuffle.partitions")
    adaptive = spark.conf.get("spark.sql.adaptive.enabled")
    spark.conf.set("spark.sql.shuffle.partitions", "16")
    spark.conf.set("spark.sql.adaptive.enabled", "false")
    try:
        run(spark, paths, MONDAY)
    finally:
        spark.conf.set("spark.sql.shuffle.partitions", shuffle_partitions)
        spark.conf.set("spark.sql.adaptive.enabled", adaptive)
    week_dirs = glob.glob(f"{root}/weekly_customer_summary/week_start=*")
    assert week_dirs
    for week_dir in week_dirs:
        parts = glob.glob(os.path.join(week_dir, "part-*.parquet"))
        assert len(parts) == 1, f"{os.path.basename(week_dir)} holds {len(parts)} files"


def test_run_does_not_execute_the_plan_twice(spark, lake, monkeypatch):
    """Counting the result for a log line runs the whole read and aggregate again."""
    paths = _warehouse(spark, lake)
    calls: list[int] = []
    original = DataFrame.count

    def counting(self: DataFrame) -> int:
        calls.append(1)
        return original(self)

    monkeypatch.setattr(DataFrame, "count", counting)
    run(spark, paths, RANGE)
    assert calls == []


def test_lake_paths_owns_the_new_tables():
    paths = LakePaths("/lake")
    assert paths.customers == "/lake/customers"
    assert paths.weekly_customer_summary == "/lake/weekly_customer_summary"


def test_written_columns_match_the_declared_schema(spark, lake):
    paths = _warehouse(spark, lake)
    run(spark, paths, RANGE)
    written = _weekly_table(spark, lake)
    assert written.columns == [f.name for f in WEEKLY_CUSTOMER_SCHEMA.fields]


@pytest.mark.parametrize("week", ["2026-07-27", "2026-08-03"])
def test_weekly_totals(spark, lake, week):
    paths = _warehouse(spark, lake)
    rows = {
        r.customer_id: r
        for r in weekly_summary(spark, paths, RANGE).filter(f"week_start = '{week}'").collect()
    }
    factor = 2 if week == "2026-07-27" else 1
    assert rows[1].paid_total == Decimal("10.50") * factor
    assert rows[2].paid_total == Decimal("20.50") * factor
    assert rows[3].paid_total == Decimal("91.00") * factor
    assert rows[1].order_count == 2 * factor
