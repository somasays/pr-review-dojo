from decimal import Decimal

from chispa import assert_df_equality

from app.jobs.daily_enrichment import enrich, main
from app.jobs.schemas import CUSTOMER_ENRICHMENT_SCHEMA


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


def test_main_writes_only_the_requested_partitions(spark, lake):
    main(["--root", lake, "--start", "2026-08-01", "--end", "2026-08-02"])
    written = spark.read.parquet(f"{lake}/customer_daily_enrichment")
    assert {r.dt for r in written.select("dt").distinct().collect()} == {
        "2026-08-01",
        "2026-08-02",
    }
    assert written.count() == 6
