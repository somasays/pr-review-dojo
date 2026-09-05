from datetime import UTC, datetime
from decimal import Decimal

from chispa import assert_df_equality

from app.jobs.daily_enrichment import enrich, main
from app.jobs.schemas import CUSTOMER_ENRICHMENT_SCHEMA, ORDERS_SCHEMA


def test_enrich_metrics_for_one_day(spark, lake):
    actual = enrich(spark, lake, "2026-08-01", "2026-08-01", dry_run=True).orderBy("customer_id")
    expected = spark.createDataFrame(
        [
            (1, 2, Decimal("10.50"), Decimal("25.50"), 0, 0, 3, "2026-08-01"),
            (2, 2, Decimal("20.50"), Decimal("35.50"), 1, 1, 4, "2026-08-01"),
            (3, 2, Decimal("91.00"), Decimal("45.50"), 1, 2, 5, "2026-08-01"),
        ],
        CUSTOMER_ENRICHMENT_SCHEMA,
    )
    assert_df_equality(actual, expected, ignore_nullable=True)


def test_large_order_flag(spark, tmp_path):
    root = str(tmp_path / "lake")
    rows = [
        (1, 1, "paid", "USD", Decimal("49.00"), datetime(2026, 8, 1, 9, tzinfo=UTC), "2026-08-01"),
        (2, 1, "paid", "USD", Decimal("51.00"), datetime(2026, 8, 1, 10, tzinfo=UTC), "2026-08-01"),
    ]
    spark.createDataFrame(rows, ORDERS_SCHEMA).write.mode("overwrite").partitionBy("dt").parquet(
        f"{root}/orders"
    )
    actual = enrich(spark, root, "2026-08-01", "2026-08-01", dry_run=True).collect()[0]
    assert actual.large_order_count == 1


def test_main_writes_only_the_requested_partitions(spark, lake):
    main(["--root", lake, "--start", "2026-08-01", "--end", "2026-08-02"])
    written = spark.read.parquet(f"{lake}/customer_daily_enrichment")
    assert {r.dt for r in written.select("dt").distinct().collect()} == {
        "2026-08-01",
        "2026-08-02",
    }
    assert written.count() == 6
