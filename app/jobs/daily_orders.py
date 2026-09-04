"""Daily order aggregation batch job.

Reads orders for one partition (dt) and writes one row per customer per day
to the daily_customer_orders table, also partitioned by dt. Rerunning a day
overwrites only that partition.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from app.domain.dates import DateRange, parse_dt
from app.jobs.schemas import ORDERS_SCHEMA
from app.jobs.spark_session import get_spark

log = logging.getLogger(__name__)


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
    def customers(self) -> str:
        return f"{self.root}/customers"

    @property
    def weekly_customer_summary(self) -> str:
        return f"{self.root}/weekly_customer_summary"


def read_orders(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    """Read only the partitions in the range. Never a full scan."""
    keys = days.partition_keys()
    return (
        spark.read.schema(ORDERS_SCHEMA)
        .option("basePath", paths.orders)
        .parquet(paths.orders)
        .filter(F.col("dt").isin(keys))
    )


def aggregate_daily(orders: DataFrame) -> DataFrame:
    """One row per (customer_id, dt)."""
    paid = F.when(F.col("status").isin("paid", "shipped", "delivered"), F.col("total")).otherwise(
        F.lit(0).cast("decimal(12,2)")
    )
    cancelled = F.when(F.col("status") == "cancelled", 1).otherwise(0)
    return (
        orders.groupBy("customer_id", "dt")
        .agg(
            F.count("order_id").cast("int").alias("order_count"),
            F.sum(paid).cast("decimal(14,2)").alias("paid_total"),
            F.sum(cancelled).cast("int").alias("cancelled_count"),
        )
        .select("customer_id", "order_count", "paid_total", "cancelled_count", "dt")
    )


def write_daily(df: DataFrame, paths: LakePaths) -> None:
    # Dynamic partition overwrite: only the partitions present in df are replaced.
    df.repartition("dt").write.mode("overwrite").partitionBy("dt").parquet(
        paths.daily_customer_orders
    )


def run(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    log.info("aggregating orders for %s..%s", days.start, days.end)
    orders = read_orders(spark, paths, days)
    daily = aggregate_daily(orders)
    write_daily(daily, paths)
    return daily


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Daily order aggregation")
    parser.add_argument("--root", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    args = parser.parse_args(argv)
    days = DateRange(parse_dt(args.start), parse_dt(args.end))
    run(get_spark("daily_orders"), LakePaths(args.root), days)


if __name__ == "__main__":
    main()
