from datetime import date
from decimal import Decimal

from chispa import assert_df_equality
from pyspark.sql import functions as F

from app.domain.dates import DateRange
from app.jobs.daily_orders import (
    LakePaths,
    aggregate_daily,
    read_customers,
    read_orders,
    run,
    run_backfill,
    with_customer_region,
)

DAILY_COLUMNS = (
    "customer_id int, order_count int, paid_total decimal(14,2), "
    "cancelled_count int, customer_region string, dt string"
)


def test_read_orders_filters_partitions(spark, lake):
    df = read_orders(spark, LakePaths(lake), DateRange.single(date(2026, 8, 2)))
    assert df.select("dt").distinct().collect() == [("2026-08-02",)]
    assert df.count() == 6


def test_aggregate_daily(spark, lake):
    df = read_orders(spark, LakePaths(lake), DateRange.single(date(2026, 8, 1)))
    actual = aggregate_daily(df.withColumn("region", F.lit("US-CA"))).orderBy("customer_id")
    expected = spark.createDataFrame(
        [
            (1, 2, Decimal("10.50"), 1, "US-CA", "2026-08-01"),  # paid 10.50, cancelled excluded
            (2, 2, Decimal("20.50"), 0, "US-CA", "2026-08-01"),  # paid 20.50 + pending 50.50
            (3, 2, Decimal("91.00"), 0, "US-CA", "2026-08-01"),  # shipped 30.50 + delivered 60.50
        ],
        DAILY_COLUMNS,
    )
    assert_df_equality(actual, expected, ignore_nullable=True)


def test_with_customer_region_keeps_every_order(spark, lake):
    paths = LakePaths(lake)
    orders = read_orders(spark, paths, DateRange.single(date(2026, 8, 1)))
    labeled = with_customer_region(spark, orders, read_customers(spark, paths))
    assert labeled.count() == orders.count()
    regions = {(r.customer_id, r.region) for r in labeled.select("customer_id", "region").collect()}
    assert regions == {(1, "US-CA"), (2, "US-NY"), (3, "EU-DE")}


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


def test_run_writes_the_finance_extract(spark, lake):
    paths = LakePaths(lake)
    run(spark, paths, DateRange.single(date(2026, 8, 1)))
    extract = spark.read.option("header", True).csv(f"{paths.extracts}/2026-08-01_2026-08-01.csv")
    assert extract.count() == 3
    assert "paid_total" in extract.columns


def test_backfill_splits_the_range_into_chunks(spark, lake):
    paths = LakePaths(lake)
    chunks = run_backfill(spark, paths, DateRange(date(2026, 8, 1), date(2026, 8, 3)), chunk_days=2)
    assert [(c.start, c.end) for c in chunks] == [
        (date(2026, 8, 1), date(2026, 8, 2)),
        (date(2026, 8, 3), date(2026, 8, 3)),
    ]
