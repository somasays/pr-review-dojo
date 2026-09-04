"""Weekly customer summary batch job.

Rolls the daily_customer_orders table up to one row per customer per week and
attaches the customer's region from the customers dimension. Weeks start on
Monday, and the week partition key is the Monday formatted as YYYY-MM-DD.
Rerunning a week overwrites only that week's partition.
"""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from pyspark.sql import Column, DataFrame, SparkSession
from pyspark.sql import functions as F

from app.domain.dates import DateRange, parse_dt, to_dt
from app.jobs.daily_orders import LakePaths
from app.jobs.schemas import CUSTOMERS_SCHEMA, DAILY_CUSTOMER_SCHEMA
from app.jobs.spark_session import get_spark

log = logging.getLogger(__name__)


def week_start(day: date) -> date:
    """The Monday on or before day."""
    return day - timedelta(days=day.weekday())


def week_keys(days: DateRange) -> list[str]:
    """Every week partition key the range touches, in calendar order."""
    return sorted({to_dt(week_start(d)) for d in days})


def covered_days(days: DateRange) -> DateRange:
    """Every day of every week the range touches, so no week is summed in part."""
    return DateRange(week_start(days.start), week_start(days.end) + timedelta(days=6))


def week_start_column() -> Column:
    """The Monday of the week each dt partition key falls in."""
    return F.date_format(F.date_trunc("week", F.to_date(F.col("dt"), "yyyy-MM-dd")), "yyyy-MM-dd")


def read_daily(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    """Read only the dt partitions the requested weeks cover. Never a full scan."""
    return (
        spark.read.schema(DAILY_CUSTOMER_SCHEMA)
        .option("basePath", paths.daily_customer_orders)
        .parquet(paths.daily_customer_orders)
        .filter(F.col("dt").isin(covered_days(days).partition_keys()))
    )


def read_customers(spark: SparkSession, paths: LakePaths) -> DataFrame:
    """Read the customers dimension. One row per customer."""
    return spark.read.schema(CUSTOMERS_SCHEMA).parquet(f"{paths.root}/customers")


def roll_up_weeks(daily: DataFrame) -> DataFrame:
    """One row per (customer_id, week_start)."""
    return (
        daily.withColumn("week_start", week_start_column())
        .groupBy("customer_id", "week_start")
        .agg(
            F.sum("order_count").cast("int").alias("n_orders"),
            F.sum("paid_total").cast("decimal(14,2)").alias("total"),
            F.sum("cancelled_count").cast("int").alias("cancelled_count"),
        )
    )


def weekly_summary(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    """The weekly report: customer weeks with the region from the dimension."""
    daily = read_daily(spark, paths, days)
    customers = read_customers(spark, paths)
    weekly = roll_up_weeks(daily)
    return weekly.join(customers, "customer_id", "left").select(
        "customer_id",
        F.coalesce(F.col("region"), F.lit("unknown")).alias("region"),
        "n_orders",
        "total",
        "cancelled_count",
        "week_start",
    )


def write_weekly(df: DataFrame, paths: LakePaths) -> None:
    # Dynamic partition overwrite: only the weeks present in df are replaced.
    df.repartition("week_start").write.mode("overwrite").partitionBy("week_start").parquet(
        f"{paths.root}/weekly_customer_summary"
    )


def run(spark: SparkSession, paths: LakePaths, days: DateRange) -> DataFrame:
    log.info("summarizing weeks %s", ", ".join(week_keys(days)))
    weekly = weekly_summary(spark, paths, days)
    log.info("writing %d customer weeks", weekly.count())
    write_weekly(weekly, paths)
    return weekly


def is_current_week(day: date) -> bool:
    """True when day falls in the week that has not finished yet."""
    return week_start(day) == week_start(date.today())


def backfill_weeks(
    spark: SparkSession,
    paths: LakePaths,
    days: DateRange,
    include_current_week: bool = False,
) -> None:
    """Run the weekly summary one week at a time, so a long backfill does not
    have to hold every week's shuffle in flight at once."""
    chunks: list[DateRange] = []
    cur = days.start
    while cur <= days.end:
        chunk_end = min(cur + timedelta(days=6), days.end)
        chunks.append(DateRange(cur, chunk_end))
        cur = chunk_end + timedelta(days=1)
    for chunk in chunks:
        if not include_current_week and is_current_week(chunk.end):
            continue
        run(spark, paths, chunk)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Weekly customer summary")
    parser.add_argument("--root", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--backfill", action="store_true", help="run one week at a time")
    parser.add_argument(
        "--include-current-week",
        action="store_true",
        default=False,
        help="also summarize the in-progress week during a backfill",
    )
    args = parser.parse_args(argv)
    days = DateRange(parse_dt(args.start), parse_dt(args.end))
    spark = get_spark("weekly_summary")
    if args.backfill:
        backfill_weeks(spark, LakePaths(args.root), days, args.include_current_week)
    else:
        run(spark, LakePaths(args.root), days)


if __name__ == "__main__":
    main()
