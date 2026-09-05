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
from decimal import Decimal

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.streaming import StreamingQuery

from app.jobs.schemas import (
    PAYMENT_ALERTS_SCHEMA,
    PAYMENT_BATCH_METRICS_SCHEMA,
    PAYMENT_EVENTS_SCHEMA,
)
from app.jobs.spark_session import get_spark

log = logging.getLogger(__name__)


def start(
    spark: SparkSession,
    source_dir: str,
    root: str,
    checkpoint: str,
    available_now: bool = False,
) -> StreamingQuery:
    # read the raw lines from the drop directory
    raw = spark.readStream.option("maxFilesPerTrigger", 10).text(source_dir)

    def _sink(df: DataFrame, bid: int) -> None:
        # grab a session handle off the batch
        s = df.sparkSession
        # turn each line into columns
        parsed = (
            df.select(F.from_json(F.col("value"), PAYMENT_EVENTS_SCHEMA).alias("e"))
            .select("e.*")
            .filter(F.col("event_id").isNotNull())
        )
        # nothing to do for an empty trigger
        if parsed.isEmpty():
            log.info("batch %d empty", bid)
            return
        # dedupe: keep the newest event per payment
        w = Window.partitionBy("payment_id").orderBy(
            F.col("event_time").desc(), F.col("event_id").desc()
        )
        incoming = (
            parsed.withColumn("_rn", F.row_number().over(w))
            .filter(F.col("_rn") == 1)
            .drop("_rn")
            .withColumn("_batch_id", F.lit(bid))
        )
        # merge with whatever is already in the table
        tgt = f"{root}/payments_latest"
        if os.path.exists(tgt):
            existing = s.read.parquet(tgt)
            u = existing.unionByName(incoming, allowMissingColumns=True)
            w2 = Window.partitionBy("payment_id").orderBy(
                F.col("event_time").desc(), F.col("event_id").desc()
            )
            merged = (
                u.withColumn("_rn", F.row_number().over(w2)).filter(F.col("_rn") == 1).drop("_rn")
            )
        else:
            merged = incoming
        # Stage first: Spark cannot overwrite a path it is still reading from.
        tmp = f"{tgt}__staging"
        merged.write.mode("overwrite").parquet(tmp)
        # copy staging over the target
        s.read.parquet(tmp).write.mode("overwrite").parquet(tgt)
        # delete the staging directory
        shutil.rmtree(tmp, ignore_errors=True)

        # count what this batch did
        agg = incoming.groupBy().agg(
            F.count(F.lit(1)).cast("int").alias("event_count"),
            F.sum(F.when(F.col("status") == "failed", 1).otherwise(0))
            .cast("int")
            .alias("failed_count"),
            F.coalesce(F.sum(F.when(F.col("status") == "captured", F.col("amount"))), F.lit(0))
            .cast("decimal(14,2)")
            .alias("captured_amount"),
        )
        r = agg.first()
        n = int(r["event_count"])
        bad = int(r["failed_count"])
        amt = Decimal(r["captured_amount"])
        # write one counter row for the batch
        mdf = s.createDataFrame([(bid, n, bad, amt)], PAYMENT_BATCH_METRICS_SCHEMA)
        mpath = f"{root}/payment_batch_metrics"
        if os.path.exists(mpath):
            mdf.write.mode("append").parquet(mpath)
        else:
            mdf.write.mode("overwrite").parquet(mpath)

        # alert when too many payments in the batch failed
        if n >= 4 and bad / n > 0.25:
            adf = s.createDataFrame([(bid, "high_failure_rate", bad, n)], PAYMENT_ALERTS_SCHEMA)
            apath = f"{root}/payment_alerts"
            if os.path.exists(apath):
                adf.write.mode("append").parquet(apath)
            else:
                adf.write.mode("overwrite").parquet(apath)
            # tell the on-call channel
            log.warning("batch %d: %d of %d payments failed", bid, bad, n)
        # log the batch summary
        log.info("batch %d: %d events, %d failed, %s captured", bid, n, bad, amt)

    writer = raw.writeStream.option("checkpointLocation", checkpoint).foreachBatch(_sink)
    if available_now:
        writer = writer.trigger(availableNow=True)
    else:
        writer = writer.trigger(processingTime="30 seconds")
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
