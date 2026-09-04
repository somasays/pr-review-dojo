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

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from app.domain.dates import DateRange, parse_dt
from app.jobs.daily_orders import LakePaths
from app.jobs.schemas import ORDERS_SCHEMA
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

    df = (
        spark.read.schema(ORDERS_SCHEMA)
        .option("basePath", paths.orders)
        .parquet(paths.orders)
        .filter(F.col("dt").isin(days.partition_keys()))
    )

    df = df.withColumn(
        "is_paid",
        F.when(F.col("status").isin(*PAID_STATUSES), F.lit(1)).otherwise(F.lit(0)),
    )
    df = df.withColumn(
        "paid_amt",
        F.when(F.col("is_paid") == 1, F.col("total")).otherwise(F.lit(0)),
    )
    df = df.withColumn("paid_amt", F.col("paid_amt").cast("decimal(12,2)"))
    df = df.withColumn("total_amt", F.col("total").cast("decimal(12,2)"))
    df = df.withColumn("dt_str", F.col("dt").cast("string"))
    df = df.withColumn("order_hour", F.hour(F.col("created_at")))
    df = df.withColumn("order_hour_int", F.col("order_hour").cast("int"))
    df = df.withColumn(
        "is_large",
        F.when(F.col("total_amt") >= F.lit(LARGE_ORDER_TOTAL), F.lit(1)).otherwise(F.lit(0)),
    )
    df = df.withColumn("large_int", F.col("is_large").cast("int"))
    df = df.withColumn("one", F.lit(1))
    df = df.withColumn("one_int", F.col("one").cast("int"))
    df = df.withColumn("paid_amt_wide", F.col("paid_amt").cast("decimal(14,2)"))

    grouped = df.groupBy("customer_id", "dt_str").agg(
        F.sum("one_int").cast("int").alias("order_count"),
        F.sum("paid_amt_wide").cast("decimal(14,2)").alias("paid_total"),
        F.sum("total_amt").alias("gross_total"),
        F.sum("large_int").cast("int").alias("large_order_count"),
        F.min("order_hour_int").cast("int").alias("first_order_hour"),
        F.max("order_hour_int").cast("int").alias("last_order_hour"),
    )
    grouped = grouped.withColumn("avg_raw", F.col("gross_total") / F.col("order_count"))
    grouped = grouped.withColumn("avg_order_value", F.col("avg_raw").cast("decimal(14,2)"))
    grouped = grouped.withColumnRenamed("dt_str", "dt")
    out = grouped.drop("gross_total", "avg_raw").select(
        "customer_id",
        "order_count",
        "paid_total",
        "avg_order_value",
        "large_order_count",
        "first_order_hour",
        "last_order_hour",
        "dt",
    )

    if not dry_run:
        # Dynamic partition overwrite: only the days present in out are replaced.
        out.repartition("dt").write.mode("overwrite").partitionBy("dt").parquet(
            paths.customer_daily_enrichment
        )
    return out


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
