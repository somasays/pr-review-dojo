"""Order events streaming job.

Consumes newline-delimited JSON order events from a directory (a stand-in
for the Kafka topic in production), deduplicates by event_id within the
watermark, and upserts the latest status per order into a parquet table via
foreachBatch. Each micro-batch write is idempotent on (order_id).

A second query keeps a running count of paid orders per customer in its own
table so the reports API can serve "orders paid so far" without scanning the
order history. A third query keeps each customer's lifetime paid total for
the same report.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.streaming import StreamingQuery

from app.jobs.schemas import ORDER_EVENTS_SCHEMA
from app.jobs.spark_session import get_spark

log = logging.getLogger(__name__)

WATERMARK = "10 minutes"
PAID_STATUS = "paid"


def read_events(spark: SparkSession, source_dir: str) -> DataFrame:
    return (
        spark.readStream.schema(ORDER_EVENTS_SCHEMA)
        .option("maxFilesPerTrigger", 10)
        .json(source_dir)
        .withWatermark("event_time", WATERMARK)
        .dropDuplicatesWithinWatermark(["event_id"])
    )


def paid_events(spark: SparkSession, source_dir: str) -> DataFrame:
    """Stream of paid order events, one row per event id."""
    return (
        spark.readStream.schema(ORDER_EVENTS_SCHEMA)
        .option("maxFilesPerTrigger", 10)
        .json(source_dir)
        .withWatermark("event_time", WATERMARK)
        .dropDuplicatesWithinWatermark(["event_id"])
        .filter(F.col("status") == PAID_STATUS)
    )


def latest_per_order(events: DataFrame) -> DataFrame:
    """Reduce a micro-batch to the newest event per order."""
    w = Window.partitionBy("order_id").orderBy(F.col("event_time").desc(), F.col("event_id").desc())
    return events.withColumn("_rn", F.row_number().over(w)).filter(F.col("_rn") == 1).drop("_rn")


def upsert_batch(batch: DataFrame, batch_id: int, target: str) -> None:
    """Merge the batch into the target table keyed by order_id.

    Reads existing rows, keeps the newer of (existing, incoming) per order,
    and rewrites. Safe to replay: the same batch produces the same table.
    """
    spark = batch.sparkSession
    incoming = latest_per_order(batch).withColumn("_batch_id", F.lit(batch_id))
    # Local filesystem paths only; the production job targets a table, not a path.
    if os.path.exists(target):
        existing = spark.read.parquet(target)
        merged = latest_per_order(existing.unionByName(incoming, allowMissingColumns=True))
    else:
        merged = incoming
    # Stage first: Spark cannot overwrite a path it is still reading from.
    staging = f"{target}__staging"
    merged.write.mode("overwrite").parquet(staging)
    spark.read.parquet(staging).write.mode("overwrite").parquet(target)
    shutil.rmtree(staging, ignore_errors=True)
    log.info("batch %d merged", batch_id)


def batch_paid_counts(batch: DataFrame) -> DataFrame:
    """Paid orders per customer in one micro-batch.

    An order can appear more than once in a batch (a retried producer send,
    or two paid events for the same order), so orders are counted once.
    """
    return (
        batch.select("customer_id", "order_id")
        .distinct()
        .groupBy("customer_id")
        .agg(F.count("*").cast("int").alias("paid_count"))
    )


def merge_paid_counts(batch: DataFrame, batch_id: int, target: str) -> None:
    """Add this batch's paid orders to the running count per customer.

    Reads the current counts, adds the counts from this batch, and rewrites
    the table with one row per customer.
    """
    spark = batch.sparkSession
    deltas = batch_paid_counts(batch)
    if os.path.exists(target):
        existing = spark.read.parquet(target)
        merged = (
            existing.unionByName(deltas)
            .groupBy("customer_id")
            .agg(F.sum("paid_count").cast("int").alias("paid_count"))
        )
    else:
        merged = deltas
    # Stage first: Spark cannot overwrite a path it is still reading from.
    staging = f"{target}__staging"
    merged.write.mode("overwrite").parquet(staging)
    spark.read.parquet(staging).write.mode("overwrite").parquet(target)
    shutil.rmtree(staging, ignore_errors=True)
    log.info("paid counts batch %d merged", batch_id)


def start(
    spark: SparkSession, source_dir: str, target: str, checkpoint: str, available_now: bool = False
) -> StreamingQuery:
    events = read_events(spark, source_dir)
    writer = events.writeStream.option("checkpointLocation", checkpoint).foreachBatch(
        lambda df, bid: upsert_batch(df, bid, target)
    )
    if available_now:
        writer = writer.trigger(availableNow=True)
    else:
        writer = writer.trigger(processingTime="30 seconds")
    return writer.start()


def start_paid_counts(
    spark: SparkSession, source_dir: str, target: str, checkpoint: str, available_now: bool = False
) -> StreamingQuery:
    events = paid_events(spark, source_dir)
    writer = (
        events.writeStream.queryName("paid_order_counts")
        .option("checkpointLocation", checkpoint)
        .foreachBatch(lambda df, bid: merge_paid_counts(df, bid, target))
    )
    if available_now:
        writer = writer.trigger(availableNow=True)
    else:
        # The counts feed a live dashboard, so it picks up new files quickly.
        writer = writer.trigger(processingTime="1 second")
    return writer.start()


def customer_running_totals(spark: SparkSession, source_dir: str) -> DataFrame:
    """Lifetime paid total per customer, for the spend-so-far report."""
    return (
        paid_events(spark, source_dir)
        .groupBy("customer_id")
        .agg(F.sum("total").alias("total_paid"))
    )


def start_customer_totals(
    spark: SparkSession, source_dir: str, target: str, checkpoint: str, available_now: bool = False
) -> StreamingQuery:
    totals = customer_running_totals(spark, source_dir)
    writer = (
        totals.writeStream.queryName("customer_running_totals")
        .outputMode("complete")
        .option("checkpointLocation", checkpoint)
        .foreachBatch(lambda df, _bid: df.write.mode("overwrite").parquet(target))
    )
    if available_now:
        writer = writer.trigger(availableNow=True)
    else:
        writer = writer.trigger(processingTime="30 seconds")
    return writer.start()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Order events stream")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--counts-target", help="paid order counts table, omit to skip that query")
    parser.add_argument("--counts-checkpoint", help="checkpoint directory for the counts query")
    parser.add_argument("--totals-target", help="customer running totals table, omit to skip")
    parser.add_argument("--totals-checkpoint", help="checkpoint directory for the totals query")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if args.counts_target and not args.counts_checkpoint:
        parser.error("--counts-target needs --counts-checkpoint")
    if args.totals_target and not args.totals_checkpoint:
        parser.error("--totals-target needs --totals-checkpoint")
    spark = get_spark("order_events")
    queries = [start(spark, args.source, args.target, args.checkpoint, args.once)]
    if args.counts_target:
        queries.append(
            start_paid_counts(
                spark, args.source, args.counts_target, args.counts_checkpoint, args.once
            )
        )
    if args.totals_target:
        queries.append(
            start_customer_totals(
                spark, args.source, args.totals_target, args.totals_checkpoint, args.once
            )
        )
    for q in queries:
        q.awaitTermination()


if __name__ == "__main__":
    main()
