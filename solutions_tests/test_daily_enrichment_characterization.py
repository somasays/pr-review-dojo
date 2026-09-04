"""Behavior pinned across the rewrite. Drives the CLI only, so the same test
runs unchanged against either shape of the module."""

import os
from decimal import Decimal

from chispa import assert_df_equality
from pyspark.sql import functions as F

from app.jobs.daily_enrichment import main
from app.jobs.schemas import CUSTOMER_ENRICHMENT_SCHEMA


def _written(spark, lake):
    return spark.read.parquet(f"{lake}/customer_daily_enrichment")


def test_metrics_for_one_day(spark, lake):
    main(["--root", lake, "--start", "2026-08-01", "--end", "2026-08-01"])
    actual = _written(spark, lake).orderBy("customer_id")
    expected = spark.createDataFrame(
        [
            (1, 2, Decimal("10.50"), Decimal("25.50"), 0, 0, 3, "2026-08-01"),
            (2, 2, Decimal("20.50"), Decimal("35.50"), 1, 1, 4, "2026-08-01"),
            (3, 2, Decimal("91.00"), Decimal("45.50"), 1, 2, 5, "2026-08-01"),
        ],
        CUSTOMER_ENRICHMENT_SCHEMA,
    )
    assert_df_equality(
        actual.select(expected.columns), expected, ignore_nullable=True, ignore_row_order=True
    )


def test_range_covers_every_requested_partition(spark, lake):
    main(["--root", lake, "--start", "2026-08-01", "--end", "2026-08-03"])
    written = _written(spark, lake)
    assert {r.dt for r in written.select("dt").distinct().collect()} == {
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    }
    assert written.count() == 9


def test_rerunning_one_day_replaces_only_that_partition(spark, lake):
    main(["--root", lake, "--start", "2026-08-01", "--end", "2026-08-02"])
    before = _written(spark, lake).count()
    main(["--root", lake, "--start", "2026-08-02", "--end", "2026-08-02"])
    after = _written(spark, lake)
    assert after.count() == before
    assert after.filter(F.col("dt") == "2026-08-01").count() == 3


def test_dry_run_writes_nothing(spark, lake):
    main(["--root", lake, "--start", "2026-08-01", "--end", "2026-08-01", "--dry-run"])
    assert not os.path.exists(f"{lake}/customer_daily_enrichment")
