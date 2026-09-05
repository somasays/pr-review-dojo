"""Daily order aggregation batch job.

Reads orders for one partition (dt) and writes one row per customer per day
to the daily_customer_orders table, also partitioned by dt. Rerunning a day
overwrites only that partition. With --backfill a long range is processed in
chunks so one catch up run does not build a single giant plan.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from app.domain.dates import DateRange, parse_dt
from app.jobs.schemas import CUSTOMERS_SCHEMA, ORDERS_SCHEMA
from app.jobs.spark_session import SKEW_SALT_BUCKETS, get_spark

log = logging.getLogger(__name__)

# Default window for a backfill run when --start/--end are not given.
DEFAULT_BACKFILL_DAYS = 30


@dataclass(frozen=True)
class LakePaths:
    root: str

    @property
    def orders(self) -> str:
        return f"{self.root}/orders"

    @property
    def daily_customer_orders(self) -> str:
        return f"{self.root}/daily_customer_orders"

    @property
    def extracts(self) -> str:
        return f"{self.root}/extracts"


def read_orders(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    """Read only the partitions in the range. Never a full scan."""
    keys = days.partition_keys()
    return (
        spark.read.schema(ORDERS_SCHEMA)
        .option("basePath", paths.orders)
        .parquet(paths.orders)
        .filter(F.col("dt").isin(keys))
    )


def read_customers(spark: SparkSession, paths: LakePaths) -> DataFrame:
    """Customers dimension, replicated once per salt bucket for the skewed join."""
    customers = spark.read.schema(CUSTOMERS_SCHEMA).parquet(f"{paths.root}/customers")
    # The dimension keeps one row per (customer_id, effective_date); keep the current one.
    latest = Window.partitionBy("customer_id").orderBy(F.col("effective_date").desc())
    current = customers.withColumn("rn", F.row_number().over(latest)).filter(F.col("rn") == 1)
    return current.select(
        "customer_id",
        "region",
        F.explode(F.sequence(F.lit(0), F.lit(SKEW_SALT_BUCKETS - 1))).alias("salt"),
    )


def with_customer_region(orders: DataFrame, customers: DataFrame) -> DataFrame:
    """Attach the customer region to each order and drop internal test accounts.

    The guest checkout customer owns most of the rows on a busy day, so the
    order side is salted and the dimension is replicated to match.
    """
    salted = orders.withColumn("salt", F.pmod(F.hash(F.col("order_id")), F.lit(SKEW_SALT_BUCKETS)))
    joined = salted.join(customers, ["customer_id", "salt"], "left").drop("salt")
    labeled = joined.withColumn("region", F.coalesce(F.col("region"), F.lit("unknown")))
    return labeled.filter(F.col("region") != "INTERNAL")


def aggregate_daily(orders: DataFrame) -> DataFrame:
    """One row per (customer_id, dt)."""
    paid = F.when(F.col("status").isin("paid", "shipped", "delivered"), F.col("total")).otherwise(
        F.lit(0).cast("decimal(12,2)")
    )
    cancelled = F.when(F.col("status") == "cancelled", 1).otherwise(0)
    return (
        orders.groupBy("customer_id", "dt", "region")
        .agg(
            F.count("order_id").cast("int").alias("order_count"),
            F.sum(paid).cast("decimal(14,2)").alias("paid_total"),
            F.sum(cancelled).cast("int").alias("cancelled_count"),
        )
        .select(
            "customer_id",
            "order_count",
            "paid_total",
            "cancelled_count",
            F.col("region").alias("customer_region"),
            "dt",
        )
    )


def write_csv_extract(df: DataFrame, path: str) -> None:
    """One CSV file for finance, no part-00000 names to explain."""
    df.coalesce(1).write.mode("overwrite").option("header", True).csv(path)


def write_daily(df: DataFrame, paths: LakePaths) -> None:
    # Dynamic partition overwrite: only the partitions present in df are replaced.
    df.repartition("dt").write.mode("overwrite").partitionBy("dt").parquet(
        paths.daily_customer_orders
    )


def _run(spark: SparkSession, paths: LakePaths, days: DateRange, orders: DataFrame) -> DataFrame:
    labeled = with_customer_region(orders, read_customers(spark, paths))
    daily = aggregate_daily(labeled).cache()
    write_daily(daily, paths)
    write_csv_extract(daily, f"{paths.extracts}/{days.start}_{days.end}.csv")
    log.info("wrote %d rows for %s..%s", daily.count(), days.start, days.end)
    return daily


def run(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    log.info("aggregating orders for %s..%s", days.start, days.end)
    orders = read_orders(spark, paths, days)
    return _run(spark, paths, days, orders)


def run_for_backfill_chunk(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    """Same aggregation as run(), but late orders are re-bucketed by when they were placed."""
    log.info("backfilling orders for %s..%s", days.start, days.end)
    orders = read_orders(spark, paths, days).withColumn(
        "dt", F.date_format(F.col("created_at"), "yyyy-MM-dd")
    )
    return _run(spark, paths, days, orders)


def run_backfill(
    spark: SparkSession, paths: LakePaths, days: DateRange, chunk_days: int = 7
) -> list[DateRange]:
    """Process a long range one chunk at a time. Returns the chunks processed."""
    chunks = days.split(chunk_days)
    for chunk in chunks:
        run_for_backfill_chunk(spark, paths, chunk)
    return chunks


def default_backfill_range(today: date | None = None) -> DateRange:
    """Trailing DEFAULT_BACKFILL_DAYS days ending yesterday, for a bare --backfill."""
    return DateRange.last_n_days(DEFAULT_BACKFILL_DAYS, today=today)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Daily order aggregation")
    parser.add_argument("--root", required=True)
    parser.add_argument("--start", help="YYYY-MM-DD inclusive, defaults to a trailing window")
    parser.add_argument("--end", help="YYYY-MM-DD inclusive, defaults to a trailing window")
    parser.add_argument("--backfill", action="store_true", help="process the range in chunks")
    parser.add_argument("--chunk-days", type=int, default=7)
    args = parser.parse_args(argv)
    if args.start and args.end:
        days = DateRange(parse_dt(args.start), parse_dt(args.end))
    else:
        days = default_backfill_range()
    spark = get_spark("daily_orders")
    paths = LakePaths(args.root)
    if args.backfill:
        run_backfill(spark, paths, days, args.chunk_days)
    else:
        run(spark, paths, days)


if __name__ == "__main__":
    main()
