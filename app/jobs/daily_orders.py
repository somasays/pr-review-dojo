"""Daily order aggregation batch job.

Reads orders for one partition (dt) and writes one row per customer per day
to the daily_customer_orders table, also partitioned by dt. Rerunning a day
overwrites only that partition. With --backfill a long range is processed in
chunks so one catch up run does not build a single giant plan.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from app.domain.dates import DateRange, parse_dt
from app.jobs.schemas import CUSTOMERS_SCHEMA, ORDERS_SCHEMA
from app.jobs.spark_session import SKEW_SALT_BUCKETS, get_spark

log = logging.getLogger(__name__)

# Finance reports on the Pacific business day.
BUSINESS_TZ = "America/Los_Angeles"


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
    return customers.select(
        "customer_id",
        "region",
        F.explode(F.sequence(F.lit(0), F.lit(SKEW_SALT_BUCKETS - 1))).alias("salt"),
    )


def with_customer_region(spark: SparkSession, orders: DataFrame, customers: DataFrame) -> DataFrame:
    """Attach the customer region to each order and drop internal test accounts.

    The guest checkout customer owns most of the rows on a busy day, so the
    order side is salted and the dimension is replicated to match.
    """
    buckets = int(spark.conf.get("spark.sql.shuffle.partitions", "200") or "200")
    salted = orders.withColumn("salt", F.pmod(F.hash(F.col("order_id")), F.lit(buckets)))
    joined = salted.join(customers, ["customer_id", "salt"], "left").drop("salt")
    return joined.filter(F.col("region") != "INTERNAL")


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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    columns = df.columns
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in df.collect():
            writer.writerow([row[c] for c in columns])


def write_daily(df: DataFrame, paths: LakePaths) -> None:
    # Dynamic partition overwrite: only the partitions present in df are replaced.
    df.repartition("dt").write.mode("overwrite").partitionBy("dt").parquet(
        paths.daily_customer_orders
    )


def run(
    spark: SparkSession, paths: LakePaths, days: DateRange, backfill: bool = False
) -> DataFrame:
    log.info("aggregating orders for %s..%s", days.start, days.end)
    orders = read_orders(spark, paths, days)
    if backfill:
        # Orders that arrive late sit in the partition of the day they were
        # loaded, so a backfill re-buckets them by when they were placed.
        orders = orders.withColumn(
            "dt", F.date_format(F.from_utc_timestamp("created_at", BUSINESS_TZ), "yyyy-MM-dd")
        )
    labeled = with_customer_region(spark, orders, read_customers(spark, paths))
    daily = aggregate_daily(labeled).cache()
    write_daily(daily, paths)
    write_csv_extract(daily, f"{paths.extracts}/{days.start}_{days.end}.csv")
    log.info("wrote %d rows for %s..%s", daily.count(), days.start, days.end)
    return daily


def run_backfill(
    spark: SparkSession, paths: LakePaths, days: DateRange, chunk_days: int = 7
) -> list[DateRange]:
    """Process a long range one chunk at a time. Returns the chunks processed."""
    chunks = days.split(chunk_days)
    for chunk in chunks:
        log.info("backfill chunk %s..%s", chunk.start, chunk.end)
        run(spark, paths, chunk, backfill=True)
    return chunks


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Daily order aggregation")
    parser.add_argument("--root", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--backfill", action="store_true", help="process the range in chunks")
    parser.add_argument("--chunk-days", type=int, default=7)
    args = parser.parse_args(argv)
    days = DateRange(parse_dt(args.start), parse_dt(args.end))
    spark = get_spark("daily_orders")
    paths = LakePaths(args.root)
    if args.backfill:
        run_backfill(spark, paths, days, args.chunk_days)
    else:
        run(spark, paths, days)


if __name__ == "__main__":
    main()
