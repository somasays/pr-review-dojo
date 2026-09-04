"""Order events streaming job.

Consumes newline-delimited JSON order events from a directory (a stand-in
for the Kafka topic in production), deduplicates by event_id within the
watermark, and upserts the latest status per order into a parquet table via
foreachBatch. Each micro-batch write is idempotent on (order_id).

A second query feeds the operations dashboard: hourly counts of status
changes, merged into a parquet table keyed by (window_start, status).
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

# Dedupe state is retained for this long past the newest event seen.
WATERMARK = "10 seconds"
COUNTS_WINDOW = "1 hour"


def read_events(spark: SparkSession, source_dir: str) -> DataFrame:
    return (
        spark.readStream.schema(ORDER_EVENTS_SCHEMA)
        .option("maxFilesPerTrigger", 10)
        .json(source_dir)
        .withWatermark("event_time", WATERMARK)
        .dropDuplicatesWithinWatermark(["event_id"])
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


def read_count_events(spark: SparkSession, source_dir: str) -> DataFrame:
    """Reader for the dashboard counts.

    Separate from read_events so the dashboard can be restarted without
    touching the upsert query.
    """
    return (
        spark.readStream.schema(ORDER_EVENTS_SCHEMA)
        .json(source_dir)
        .withWatermark("event_time", "10 minutes")
        .dropDuplicatesWithinWatermark(["event_id"])
    )


def hourly_status_counts(events: DataFrame) -> DataFrame:
    """Count status changes per hour and status.

    One row per (window, status). The dashboard reads the newest row for
    each window, so the counts are cumulative within the window.
    """
    return (
        events.groupBy(
            # Processing time is what the dashboard shows.
            F.window(F.current_timestamp(), COUNTS_WINDOW),
            "status",
        )
        .count()
        .select(
            F.col("window.start").alias("window_start"),
            F.col("window.end").alias("window_end"),
            F.col("status"),
            F.col("count").alias("change_count"),
        )
    )


def latest_per_window(counts: DataFrame) -> DataFrame:
    """Reduce counts to the newest row per (window_start, status)."""
    w = Window.partitionBy("window_start", "status").orderBy(F.col("_batch_id").desc())
    return counts.withColumn("_rn", F.row_number().over(w)).filter(F.col("_rn") == 1).drop("_rn")


def merge_counts_batch(batch: DataFrame, batch_id: int, target: str) -> None:
    """Merge one micro-batch of counts into the dashboard table.

    Update mode emits the running count for every window the batch touched,
    so the newest row per (window_start, status) wins. Safe to replay.
    """
    spark = batch.sparkSession
    incoming = batch.withColumn("_batch_id", F.lit(batch_id))
    if os.path.exists(target):
        existing = spark.read.parquet(target)
        merged = latest_per_window(existing.unionByName(incoming, allowMissingColumns=True))
    else:
        merged = incoming
    # Stage first: Spark cannot overwrite a path it is still reading from.
    staging = f"{target}__staging"
    merged.write.mode("overwrite").parquet(staging)
    spark.read.parquet(staging).write.mode("overwrite").parquet(target)
    shutil.rmtree(staging, ignore_errors=True)
    log.info("counts batch %d merged", batch_id)


def start_hourly_counts(
    spark: SparkSession, source_dir: str, target: str, checkpoint: str, available_now: bool = False
) -> StreamingQuery:
    counts = hourly_status_counts(read_count_events(spark, source_dir))
    writer = (
        counts.writeStream.outputMode("update")
        .option("checkpointLocation", checkpoint)
        .foreachBatch(lambda df, bid: merge_counts_batch(df, bid, target))
    )
    if available_now:
        writer = writer.trigger(availableNow=True)
    else:
        writer = writer.trigger(processingTime="30 seconds")
    return writer.start()


def start(
    spark: SparkSession, source_dir: str, target: str, checkpoint: str, available_now: bool = False
) -> StreamingQuery:
    events = read_events(spark, source_dir)
    writer = events.writeStream.option("checkpointLocation", checkpoint).foreachBatch(
        lambda df, bid: upsert_batch(df, bid, target)
    )
    if available_now:
        writer = writer.trigger(once=True)
    else:
        writer = writer.trigger(processingTime="30 seconds")
    return writer.start()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Order events stream")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--checkpoint", required=True, help="checkpoint directory for the job")
    parser.add_argument(
        "--counts-target", help="parquet path for the hourly status change dashboard table"
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    spark = get_spark("order_events")
    queries = [start(spark, args.source, args.target, args.checkpoint, args.once)]
    if args.counts_target:
        queries.append(
            start_hourly_counts(spark, args.source, args.counts_target, args.checkpoint, args.once)
        )
    for q in queries:
        log.info("query %s started", q.id)
        # One-shot runs should not hang CI.
        q.awaitTermination(timeout=30 if args.once else None)


if __name__ == "__main__":
    main()
