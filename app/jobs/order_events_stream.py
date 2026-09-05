"""Order events streaming job.

Consumes newline-delimited JSON order events from a directory (a stand-in
for the Kafka topic in production), deduplicates repeated status transitions
within the watermark, and upserts the latest status per order into a parquet
table via foreachBatch. Each micro-batch write is idempotent on (order_id).

Lines that cannot be parsed are routed to a dead letter table by a second
query so the upsert path never sees them, and a third query keeps an hourly
count of events by status for the ops dashboard.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil

from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.streaming import DataStreamWriter, StreamingQuery

from app.jobs.schemas import CORRUPT_COLUMN, ORDER_EVENTS_RAW_SCHEMA, ORDER_EVENTS_SCHEMA
from app.jobs.spark_session import get_spark
from app.services.config import get_settings
from app.services.notification import InMemorySender, NotificationService
from app.services.retry import RetryPolicy, retry

log = logging.getLogger(__name__)

WATERMARK = "10 minutes"
COUNT_WINDOW = "1 hour"
COUNTS_TRIGGER = "30 seconds"
OPS_EMAIL = "ops@example.com"
# Bumped whenever the dedupe key changes: the state schema is not compatible
# across that change, so the query needs a fresh state directory.
STATE_VERSION = 2


def malformed() -> Column:
    """A record is unusable if it failed to parse or is missing a required field.

    The reader cannot enforce the non-nullable fields of ORDER_EVENTS_SCHEMA,
    so a well formed JSON line with a null order_id parses cleanly. Both the
    upsert query and the dead letter query route on this one predicate so
    every record lands in exactly one of them.
    """
    return (
        F.col(CORRUPT_COLUMN).isNotNull()
        | F.col("order_id").isNull()
        | F.col("status").isNull()
        | F.col("event_time").isNull()
    )


def read_events(spark: SparkSession, source_dir: str) -> DataFrame:
    """Parsed events, with the lines that failed to parse left for the dead letter query."""
    return (
        spark.readStream.schema(ORDER_EVENTS_RAW_SCHEMA)
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", CORRUPT_COLUMN)
        .option("maxFilesPerTrigger", 10)
        .json(source_dir)
        .filter(~malformed())
        .drop(CORRUPT_COLUMN)
        .withWatermark("event_time", WATERMARK)
        .dropDuplicatesWithinWatermark(["order_id", "status"])
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


def dead_letter_source(spark: SparkSession, source_dir: str) -> DataFrame:
    """Raw view of the source used by the dead letter query."""
    return (
        spark.readStream.schema(ORDER_EVENTS_RAW_SCHEMA)
        .option("mode", "PERMISSIVE")
        .option("columnNameOfCorruptRecord", CORRUPT_COLUMN)
        .option("maxFilesPerTrigger", 10)
        .json(source_dir)
    )


def dead_letter_batch(
    batch: DataFrame, batch_id: int, dlq_target: str, notifier: NotificationService
) -> None:
    """Write the rejected records of one micro-batch, partitioned by batch id."""
    rejects = batch.filter(malformed())
    rows = rejects.collect()
    if not rows:
        return
    (
        rejects.withColumn("_batch_id", F.lit(batch_id))
        .write.mode("overwrite")
        .partitionBy("_batch_id")
        .parquet(dlq_target)
    )
    # After the write, and keyed by the batch, so a replayed batch does not alert twice.
    notifier.dead_letter_alert(OPS_EMAIL, len(rows), dedupe_key=f"dead-letter:{batch_id}")
    log.info("batch %d dead lettered %d records", batch_id, len(rows))


def write_counts(counts: DataFrame, batch_id: int, target: str, debug: bool) -> None:
    """Flatten the window struct and write one batch of the hourly counts."""
    flat = counts.select(
        F.col("window.start").alias("window_start"),
        F.col("window.end").alias("window_end"),
        F.col("status"),
        F.col("count"),
    ).withColumn("_batch_id", F.lit(batch_id))
    if debug:
        log.debug("batch %d writing counts to %s", batch_id, target)
    # The object store write can fail transiently; the shared retry policy covers it.
    retry(
        lambda: flat.write.mode("overwrite").partitionBy("_batch_id").parquet(target),
        RetryPolicy(attempts=3, retry_on=(Exception,)),
    )


def _apply_trigger(
    writer: DataStreamWriter, available_now: bool, live_interval: str
) -> DataStreamWriter:
    """One-shot runs drain the backlog and stop; the live path polls on an interval."""
    if available_now:
        return writer.trigger(availableNow=True)
    return writer.trigger(processingTime=live_interval)


def start(
    spark: SparkSession, source_dir: str, target: str, checkpoint: str, available_now: bool = False
) -> StreamingQuery:
    events = read_events(spark, source_dir)
    writer = events.writeStream.option(
        "checkpointLocation", f"{checkpoint}/v{STATE_VERSION}"
    ).foreachBatch(lambda df, bid: upsert_batch(df, bid, target))
    writer = _apply_trigger(writer, available_now, "30 seconds")
    return writer.start()


def start_dead_letter(
    spark: SparkSession,
    source_dir: str,
    dlq_target: str,
    checkpoint: str,
    notifier: NotificationService,
    available_now: bool = False,
) -> StreamingQuery:
    """Park the lines that failed to parse so the pipeline never drops them silently."""
    rejects = dead_letter_source(spark, source_dir)
    writer = (
        rejects.writeStream.queryName("order_dead_letter")
        .option("checkpointLocation", checkpoint)
        .foreachBatch(lambda df, bid: dead_letter_batch(df, bid, dlq_target, notifier))
    )
    writer = _apply_trigger(writer, available_now, "30 seconds")
    return writer.start()


def start_status_counts(
    spark: SparkSession,
    source_dir: str,
    counts_target: str,
    checkpoint: str,
    available_now: bool = False,
) -> StreamingQuery:
    """Hourly count of events by status for the ops dashboard."""
    # Counts do not need the dedupe that read_events applies, so this reads
    # the source directly.
    events = (
        spark.readStream.schema(ORDER_EVENTS_SCHEMA)
        .option("maxFilesPerTrigger", 10)
        .json(source_dir)
        .withWatermark("event_time", WATERMARK)
    )
    counts = events.groupBy(F.window(F.col("event_time"), COUNT_WINDOW), F.col("status")).count()
    # Read once at query start, not on every micro-batch.
    debug = get_settings().env == "dev"
    writer = (
        counts.writeStream.queryName("order_status_counts")
        .outputMode("update")
        .option("checkpointLocation", checkpoint)
        .foreachBatch(lambda df, bid: write_counts(df, bid, counts_target, debug))
    )
    writer = _apply_trigger(writer, available_now, COUNTS_TRIGGER)
    return writer.start()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Order events stream")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--dead-letter-target", required=True)
    parser.add_argument("--counts-target", required=True)
    parser.add_argument("--checkpoint", required=True, help="checkpoint directory for the job")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    spark = get_spark("order_events")
    notifier = NotificationService(InMemorySender())
    queries = [
        start(spark, args.source, args.target, args.checkpoint, args.once),
        start_dead_letter(
            spark,
            args.source,
            args.dead_letter_target,
            f"{args.checkpoint}_dead_letter",
            notifier,
            available_now=args.once,
        ),
        start_status_counts(
            spark, args.source, args.counts_target, f"{args.checkpoint}_counts", args.once
        ),
    ]
    if args.once:
        for q in queries:
            q.awaitTermination()
    else:
        spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
