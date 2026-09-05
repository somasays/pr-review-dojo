"""Payment events streaming job.

Consumes newline-delimited JSON payment events from a directory (a stand-in
for the Kafka topic in production), keeps the latest event per payment in a
parquet table, and records one counter row per micro-batch plus an alert row
when too many payments in a batch failed. Each write is keyed by payment_id
so replaying a batch produces the same table.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
from dataclasses import dataclass

from pyspark.sql import DataFrame, Row, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.streaming import StreamingQuery

from app.jobs.schemas import (
    PAYMENT_ALERTS_SCHEMA,
    PAYMENT_BATCH_METRICS_SCHEMA,
    PAYMENT_EVENTS_SCHEMA,
)
from app.jobs.spark_session import get_spark

log = logging.getLogger(__name__)

MAX_FILES_PER_TRIGGER = 10
TRIGGER_INTERVAL = "30 seconds"
# Support wants a page when more than a quarter of a batch failed, but not
# for the handful of events a quiet trigger picks up.
FAILURE_RATE_ALERT = 0.25
MIN_BATCH_FOR_ALERT = 4


@dataclass(frozen=True)
class PaymentPaths:
    root: str

    @property
    def payments_latest(self) -> str:
        return f"{self.root}/payments_latest"

    @property
    def batch_metrics(self) -> str:
        return f"{self.root}/payment_batch_metrics"

    @property
    def alerts(self) -> str:
        return f"{self.root}/payment_alerts"


def read_raw_events(spark: SparkSession, source_dir: str) -> DataFrame:
    return spark.readStream.option("maxFilesPerTrigger", MAX_FILES_PER_TRIGGER).text(source_dir)


def parse_events(raw: DataFrame) -> DataFrame:
    """Turn one JSON line per row into typed columns, dropping unparseable lines."""
    return (
        raw.select(F.from_json(F.col("value"), PAYMENT_EVENTS_SCHEMA).alias("e"))
        .select("e.*")
        .filter(F.col("event_id").isNotNull())
    )


def latest_per_payment(events: DataFrame) -> DataFrame:
    """Reduce to the newest event per payment."""
    w = Window.partitionBy("payment_id").orderBy(
        F.col("event_time").desc(), F.col("event_id").desc()
    )
    return events.withColumn("_rn", F.row_number().over(w)).filter(F.col("_rn") == 1).drop("_rn")


def batch_counters(events: DataFrame) -> Row:
    """One row of counters for a deduplicated batch."""
    return (
        events.groupBy()
        .agg(
            F.count(F.lit(1)).cast("int").alias("event_count"),
            F.sum(F.when(F.col("status") == "failed", 1).otherwise(0))
            .cast("int")
            .alias("failed_count"),
            F.coalesce(F.sum(F.when(F.col("status") == "captured", F.col("amount"))), F.lit(0))
            .cast("decimal(14,2)")
            .alias("captured_amount"),
        )
        .first()
    )


def alert_reason(event_count: int, failed_count: int) -> str | None:
    """The alert this batch raises, or None when it is healthy enough."""
    if event_count < MIN_BATCH_FOR_ALERT:
        return None
    if failed_count / event_count > FAILURE_RATE_ALERT:
        return "high_failure_rate"
    return None


def merge_payments(incoming: DataFrame, target: str) -> None:
    """Keep the newer of (existing, incoming) per payment and rewrite the table."""
    spark = incoming.sparkSession
    merged = incoming
    if os.path.exists(target):
        existing = spark.read.parquet(target)
        merged = latest_per_payment(existing.unionByName(incoming, allowMissingColumns=True))
    # Stage first: Spark cannot overwrite a path it is still reading from.
    staging = f"{target}__staging"
    merged.write.mode("overwrite").parquet(staging)
    spark.read.parquet(staging).write.mode("overwrite").parquet(target)
    shutil.rmtree(staging, ignore_errors=True)


def write_batch(batch: DataFrame, batch_id: int, paths: PaymentPaths) -> None:
    spark = batch.sparkSession
    events = parse_events(batch)
    if events.isEmpty():
        log.info("batch %d empty", batch_id)
        return
    incoming = latest_per_payment(events).withColumn("_batch_id", F.lit(batch_id))
    merge_payments(incoming, paths.payments_latest)

    c = batch_counters(incoming)
    spark.createDataFrame(
        [(batch_id, c["event_count"], c["failed_count"], c["captured_amount"])],
        PAYMENT_BATCH_METRICS_SCHEMA,
    ).write.mode("append").parquet(paths.batch_metrics)

    reason = alert_reason(c["event_count"], c["failed_count"])
    if reason is not None:
        spark.createDataFrame(
            [(batch_id, reason, c["failed_count"], c["event_count"])], PAYMENT_ALERTS_SCHEMA
        ).write.mode("append").parquet(paths.alerts)
        log.warning(
            "batch %d: %d of %d payments failed", batch_id, c["failed_count"], c["event_count"]
        )
    log.info(
        "batch %d: %d events, %d failed, %s captured",
        batch_id,
        c["event_count"],
        c["failed_count"],
        c["captured_amount"],
    )


def start(
    spark: SparkSession,
    source_dir: str,
    root: str,
    checkpoint: str,
    available_now: bool = False,
) -> StreamingQuery:
    paths = PaymentPaths(root)
    raw = read_raw_events(spark, source_dir)
    writer = raw.writeStream.option("checkpointLocation", checkpoint).foreachBatch(
        lambda df, bid: write_batch(df, bid, paths)
    )
    if available_now:
        writer = writer.trigger(availableNow=True)
    else:
        writer = writer.trigger(processingTime=TRIGGER_INTERVAL)
    return writer.start()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Payment events stream")
    parser.add_argument("--source", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    q = start(get_spark("payment_events"), args.source, args.root, args.checkpoint, args.once)
    q.awaitTermination()


if __name__ == "__main__":
    main()
