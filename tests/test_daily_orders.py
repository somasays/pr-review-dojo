from datetime import date
from decimal import Decimal

from chispa import assert_df_equality
from pyspark.sql import functions as F

from app.domain.dates import DateRange
from app.jobs.daily_orders import LakePaths, aggregate_daily, read_orders, run
from app.jobs.schemas import DAILY_CUSTOMER_SCHEMA


def test_read_orders_filters_partitions(spark, lake):
    df = read_orders(spark, LakePaths(lake), DateRange.single(date(2026, 8, 2)))
    assert df.select("dt").distinct().collect() == [("2026-08-02",)]
    assert df.count() == 6


def test_aggregate_daily(spark, lake):
    df = read_orders(spark, LakePaths(lake), DateRange.single(date(2026, 8, 1)))
    actual = aggregate_daily(df).orderBy("customer_id")
    expected = spark.createDataFrame(
        [
            (1, 2, Decimal("10.50"), 1, "2026-08-01"),  # paid 10.50, cancelled 40.50 excluded
            (2, 2, Decimal("20.50"), 0, "2026-08-01"),  # paid 20.50 + pending 50.50
            (3, 2, Decimal("91.00"), 0, "2026-08-01"),  # shipped 30.50 + delivered 60.50
        ],
        DAILY_CUSTOMER_SCHEMA,
    )
    assert_df_equality(actual, expected, ignore_nullable=True)


def test_run_overwrites_only_target_partition(spark, lake):
    paths = LakePaths(lake)
    run(spark, paths, DateRange(date(2026, 8, 1), date(2026, 8, 2)))
    before = spark.read.parquet(paths.daily_customer_orders)
    assert {r.dt for r in before.select("dt").distinct().collect()} == {"2026-08-01", "2026-08-02"}
    before_count = before.count()  # materialize before the rewrite replaces the files
    run(spark, paths, DateRange.single(date(2026, 8, 2)))
    after = spark.read.parquet(paths.daily_customer_orders)
    assert after.filter(F.col("dt") == "2026-08-01").count() == 3
    assert after.count() == before_count
