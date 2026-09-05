"""Hidden tests for exercise 06."""

import ast
import glob
import inspect
import os
import typing
from datetime import date
from decimal import Decimal

import pytest
from pyspark.sql import DataFrame

from app.domain.dates import DateRange
from app.jobs.daily_orders import LakePaths
from app.jobs.daily_orders import run as run_daily
from app.jobs.fixtures import write_customers_fixture
from app.jobs.schemas import CUSTOMERS_SCHEMA, DAILY_CUSTOMER_SCHEMA
from app.jobs.weekly_summary import backfill_weeks, is_current_week, run, weekly_summary

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


def test_backfill_weeks_reuses_date_range_split():
    """The chunking has to reuse DateRange.split, not a hand-rolled loop."""
    tree = ast.parse(inspect.getsource(backfill_weeks))
    split_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "split"
    ]
    while_loops = [node for node in ast.walk(tree) if isinstance(node, ast.While)]
    assert split_calls, "backfill_weeks should call DateRange.split instead of chunking by hand"
    assert not while_loops, "backfill_weeks should not hand-roll the chunking loop"


def test_is_current_week_takes_a_fixed_today():
    """The clock has to be a parameter so the answer is deterministic."""
    sig = inspect.signature(is_current_week)
    assert "today" in sig.parameters
    assert is_current_week(date(2026, 8, 3), today=date(2026, 8, 5)) is True
    assert is_current_week(date(2026, 7, 27), today=date(2026, 8, 5)) is False


def test_backfill_weeks_has_no_boolean_mode_switch():
    """The two behaviors (include or skip the current week) must not be one
    function switched by a bool; call run() directly for the in-progress week."""
    hints = typing.get_type_hints(backfill_weeks)
    bool_params = [name for name, hint in hints.items() if hint is bool]
    assert not bool_params


def test_is_current_week_matches_a_pinned_today():
    """The shipped test must pin today instead of reading the real clock."""
    today = date(2026, 8, 5)
    assert is_current_week(date(2026, 8, 3), today=today) is True
    assert is_current_week(date(2026, 7, 27), today=today) is False


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
