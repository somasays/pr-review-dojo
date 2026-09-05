from datetime import date, timedelta
from decimal import Decimal

from pyspark.sql import functions as F

from app.domain.dates import DateRange
from app.jobs.daily_orders import LakePaths
from app.jobs.daily_orders import run as run_daily
from app.jobs.fixtures import write_customers_fixture
from app.jobs.weekly_summary import is_current_week, run, week_keys, weekly_summary

RANGE = DateRange(date(2026, 8, 1), date(2026, 8, 3))


def _warehouse(spark, lake: str) -> LakePaths:
    """Build daily_customer_orders and the customers dimension from the lake fixture."""
    paths = LakePaths(lake)
    run_daily(spark, paths, RANGE)
    write_customers_fixture(spark, lake)
    return paths


def test_week_keys_covers_every_week_the_range_touches():
    assert week_keys(RANGE) == ["2026-07-27", "2026-08-03"]
    assert week_keys(DateRange.single(date(2026, 8, 3))) == ["2026-08-03"]


def test_weekly_summary_rolls_daily_rows_into_weeks(spark, lake):
    paths = _warehouse(spark, lake)
    rows = {(r.customer_id, r.week_start): r for r in weekly_summary(spark, paths, RANGE).collect()}
    assert set(rows) == {(c, w) for c in (1, 2, 3) for w in ("2026-07-27", "2026-08-03")}
    # 2026-08-01 and 2026-08-02 both fall in the week starting Monday 2026-07-27.
    first = rows[(1, "2026-07-27")]
    assert first.n_orders == 4
    assert first.total == Decimal("21.00")
    assert first.cancelled_count == 2
    assert first.region == "US-CA"
    # 2026-08-03 is a Monday, so its week holds a single day.
    assert rows[(3, "2026-08-03")].total == Decimal("91.00")
    assert rows[(3, "2026-08-03")].region == "EU-DE"


def test_run_writes_one_partition_per_week(spark, lake):
    paths = _warehouse(spark, lake)
    run(spark, paths, RANGE)
    written = spark.read.parquet(f"{lake}/weekly_customer_summary")
    assert {r.week_start for r in written.select("week_start").distinct().collect()} == {
        "2026-07-27",
        "2026-08-03",
    }
    assert written.filter(F.col("week_start") == "2026-08-03").count() == 3


def test_is_current_week_matches_todays_actual_week():
    today = date.today()
    assert is_current_week(today) is True
    assert is_current_week(today - timedelta(days=7)) is False
