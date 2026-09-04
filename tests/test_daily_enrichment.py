from datetime import UTC, date, datetime
from decimal import Decimal

from chispa import assert_df_equality

from app.domain.dates import DateRange
from app.jobs.daily_enrichment import enrich_daily, main
from app.jobs.daily_orders import LakePaths, read_orders
from app.jobs.schemas import CUSTOMER_ENRICHMENT_SCHEMA, ORDERS_SCHEMA


def test_enrich_metrics_for_one_day(spark, lake):
    orders = read_orders(spark, LakePaths(lake), DateRange.single(date(2026, 8, 1)))
    actual = enrich_daily(orders).orderBy("customer_id")
    expected = spark.createDataFrame(
        [
            (1, 2, Decimal("10.50"), Decimal("25.50"), 0, 0, 3, "2026-08-01"),
            (2, 2, Decimal("20.50"), Decimal("35.50"), 1, 1, 4, "2026-08-01"),
            (3, 2, Decimal("91.00"), Decimal("45.50"), 1, 2, 5, "2026-08-01"),
        ],
        CUSTOMER_ENRICHMENT_SCHEMA,
    )
    assert_df_equality(actual, expected, ignore_nullable=True)


def test_large_order_flag(spark):
    dt = "2026-08-01"
    orders = spark.createDataFrame(
        [
            (1, 1, "paid", "USD", Decimal("49.00"), datetime(2026, 8, 1, 9, tzinfo=UTC), dt),
            (2, 1, "paid", "USD", Decimal("51.00"), datetime(2026, 8, 1, 10, tzinfo=UTC), dt),
        ],
        ORDERS_SCHEMA,
    )
    actual = enrich_daily(orders).collect()[0]
    assert actual.large_order_count == 1


def test_main_writes_only_the_requested_partitions(spark, lake):
    main(["--root", lake, "--start", "2026-08-01", "--end", "2026-08-02"])
    written = spark.read.parquet(f"{lake}/customer_daily_enrichment")
    assert {r.dt for r in written.select("dt").distinct().collect()} == {
        "2026-08-01",
        "2026-08-02",
    }
    assert written.count() == 6
