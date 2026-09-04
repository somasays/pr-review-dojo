"""Customer daily enrichment batch job.

Reads the order partitions for a date range and writes the
customer_daily_enrichment table: one row per customer per day with the
engagement metrics the retention dashboard asks for. Rerunning a day
overwrites only that day's partition.
"""

from __future__ import annotations

import argparse
import logging
from decimal import Decimal

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F

from app.domain.dates import DateRange, parse_dt
from app.jobs.daily_orders import LakePaths, read_orders
from app.jobs.spark_session import get_spark

log = logging.getLogger(__name__)

# An order at or above this total counts as a large order for the dashboard.
LARGE_ORDER_TOTAL = Decimal("50.00")
BACKFILL_DAYS = 7

PAID_STATUSES = ("paid", "shipped", "delivered")


def enrich(
    spark: SparkSession,
    paths: LakePaths,
    start: str,
    end: str,
    backfill: bool = False,
    dry_run: bool = False,
) -> DataFrame:
    """Build the enrichment table for a range of days and write it out."""
    if backfill:
        days = DateRange.last_n_days(BACKFILL_DAYS)
    else:
        days = DateRange(parse_dt(start), parse_dt(end))
    log.info("enriching orders for %s..%s", days.start, days.end)
    out = enrich_daily(read_orders(spark, paths, days))
    if not dry_run:
        write_enrichment(out, paths)
    return out


def enrich_daily(orders: DataFrame) -> DataFrame:
    """One row per (customer_id, dt)."""
    paid: Column = F.when(F.col("status").isin(*PAID_STATUSES), F.col("total")).otherwise(
        F.lit(0).cast("decimal(12,2)")
    )
    large: Column = F.when(F.col("total") >= F.lit(LARGE_ORDER_TOTAL), 1).otherwise(0)
    hour: Column = F.hour("created_at")
    return (
        orders.groupBy("customer_id", "dt")
        .agg(
            F.count("order_id").cast("int").alias("order_count"),
            F.sum(paid).cast("decimal(14,2)").alias("paid_total"),
            (F.sum("total") / F.count("order_id")).cast("decimal(14,2)").alias("avg_order_value"),
            F.sum(large).cast("int").alias("large_order_count"),
            F.min(hour).cast("int").alias("first_order_hour"),
            F.max(hour).cast("int").alias("last_order_hour"),
        )
        .select(
            "customer_id",
            "order_count",
            "paid_total",
            "avg_order_value",
            "large_order_count",
            "first_order_hour",
            "last_order_hour",
            "dt",
        )
    )


def write_enrichment(df: DataFrame, paths: LakePaths) -> None:
    # Dynamic partition overwrite: only the partitions present in df are replaced.
    df.repartition("dt").write.mode("overwrite").partitionBy("dt").parquet(
        paths.customer_daily_enrichment
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Customer daily enrichment")
    parser.add_argument("--root", required=True)
    parser.add_argument("--start", default="", help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", default="", help="YYYY-MM-DD inclusive")
    parser.add_argument("--backfill", action="store_true", help=f"last {BACKFILL_DAYS} days")
    parser.add_argument("--dry-run", action="store_true", help="compute but do not write")
    args = parser.parse_args(argv)
    enrich(
        get_spark("daily_enrichment"),
        LakePaths(args.root),
        args.start,
        args.end,
        backfill=args.backfill,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
