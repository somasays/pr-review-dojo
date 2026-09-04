"""Daily order aggregation batch job.

Reads orders for one partition (dt) and writes one row per customer per day
to the daily_customer_orders table, also partitioned by dt. Rerunning a day
overwrites only that partition.

The same run also builds daily_product_sales, one row per (sku, dt), by
joining the order lines for the range to the products dimension so merchandising
gets product names and categories alongside the numbers.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from app.domain.dates import DateRange, parse_dt
from app.jobs.schemas import ORDER_LINES_SCHEMA, ORDERS_SCHEMA, PRODUCTS_SCHEMA
from app.jobs.spark_session import get_spark

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LakePaths:
    root: str

    @property
    def orders(self) -> str:
        return f"{self.root}/orders"

    @property
    def order_lines(self) -> str:
        return f"{self.root}/order_lines"

    @property
    def products(self) -> str:
        return f"{self.root}/products"

    @property
    def daily_customer_orders(self) -> str:
        return f"{self.root}/daily_customer_orders"

    @property
    def daily_product_sales(self) -> str:
        return f"{self.root}/daily_product_sales"


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


def read_order_lines(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    """Read only the partitions in the range. Never a full scan."""
    keys = days.partition_keys()
    return (
        spark.read.schema(ORDER_LINES_SCHEMA)
        .option("basePath", paths.order_lines)
        .parquet(paths.order_lines)
        .filter(F.col("dt").isin(keys))
    )


def read_products(spark: SparkSession, paths: LakePaths) -> DataFrame:
    """Products dimension, one row per sku.

    The table keeps one row per (sku, effective_date), so only the current
    version of each sku is returned. Joining the raw table on sku alone would
    emit one order line per historical price.
    """
    versions = (
        spark.read.schema(PRODUCTS_SCHEMA)
        .parquet(paths.products)
        .withColumn(
            "_rank",
            F.row_number().over(Window.partitionBy("sku").orderBy(F.col("effective_date").desc())),
        )
    )
    return versions.filter(F.col("_rank") == 1).drop("_rank")


def aggregate_by_product(lines: DataFrame, products: DataFrame) -> DataFrame:
    """One row per (sku, dt), with the product name and category attached."""
    line_revenue = F.when(
        F.col("status").isin("paid", "shipped", "delivered"), F.col("line_total")
    ).otherwise(F.lit(0).cast("decimal(12,2)"))
    # Merchandising wants "Home Office", not "home_office".
    category = F.coalesce(F.col("category"), F.lit("unknown"))
    joined = (
        lines.join(products, "sku", "left")
        .withColumn("category", category)
        .filter(F.col("category") != "INTERNAL")
        .withColumn("category_label", F.initcap(F.regexp_replace(F.col("category"), "_", " ")))
    )
    return (
        joined.groupBy("sku", "dt")
        .agg(
            F.first("name").alias("product_name"),
            F.first("category_label").alias("category_label"),
            F.sum("quantity").cast("int").alias("units_sold"),
            F.sum(line_revenue).cast("decimal(14,2)").alias("revenue"),
            F.avg(F.col("line_total")).cast("decimal(14,2)").alias("avg_line_value"),
        )
        .select(
            "sku",
            "product_name",
            "category_label",
            "units_sold",
            "revenue",
            "avg_line_value",
            "dt",
        )
    )


def write_daily(df: DataFrame, paths: LakePaths) -> None:
    # Dynamic partition overwrite: only the partitions present in df are replaced.
    df.repartition("dt").write.mode("overwrite").partitionBy("dt").parquet(
        paths.daily_customer_orders
    )


def write_daily_products(df: DataFrame, paths: LakePaths) -> None:
    # Dynamic partition overwrite: only the partitions present in df are replaced.
    df.repartition("dt").write.mode("overwrite").partitionBy("dt").parquet(
        paths.daily_product_sales
    )


def run(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    log.info("aggregating orders for %s..%s", days.start, days.end)
    orders = read_orders(spark, paths, days)
    daily = aggregate_daily(orders)
    write_daily(daily, paths)

    lines = read_order_lines(spark, paths, days)
    products = read_products(spark, paths)
    write_daily_products(aggregate_by_product(lines, products), paths)
    return daily


def run_backfill(
    spark: SparkSession, paths: LakePaths, days: DateRange, chunk_days: int = 7
) -> None:
    """Chunk a long range so each piece is written and read like any other day."""
    products = read_products(spark, paths)
    for chunk in days.split(chunk_days):
        lines = read_order_lines(spark, paths, chunk)
        write_daily_products(aggregate_by_product(lines, products), paths)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Daily order aggregation")
    parser.add_argument("--root", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument(
        "--backfill", action="store_true", help="chunk a long range instead of one pass"
    )
    args = parser.parse_args(argv)
    days = DateRange(parse_dt(args.start), parse_dt(args.end))
    spark = get_spark("daily_orders")
    if args.backfill:
        run_backfill(spark, LakePaths(args.root), days)
    else:
        run(spark, LakePaths(args.root), days)


if __name__ == "__main__":
    main()
