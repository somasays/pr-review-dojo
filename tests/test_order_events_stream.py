import json
from datetime import UTC, datetime
from pathlib import Path

from pyspark.sql import functions as F

from app.jobs.fixtures import write_events_fixture, write_status_change_fixture
from app.jobs.order_events_stream import start, start_hourly_counts


def _run_once(spark, src, target, ck):
    q = start(spark, str(src), str(target), str(ck), available_now=True)
    q.awaitTermination()


def test_latest_status_per_order_and_dedupe(spark, tmp_path: Path):
    src, target, ck = tmp_path / "events", tmp_path / "orders_latest", tmp_path / "ck"
    write_events_fixture(str(src), with_duplicate=True)
    _run_once(spark, src, target, ck)
    rows = {r.order_id: r.status for r in spark.read.parquet(str(target)).collect()}
    assert rows == {1: "paid", 2: "pending_payment"}


def test_second_batch_upserts_and_replay_is_idempotent(spark, tmp_path: Path):
    src, target, ck = tmp_path / "events", tmp_path / "orders_latest", tmp_path / "ck"
    write_events_fixture(str(src))
    _run_once(spark, src, target, ck)
    later = datetime(2026, 8, 1, 12, 5, tzinfo=UTC).isoformat()
    with open(src / "events-0002.json", "w") as f:
        f.write(
            json.dumps(
                {
                    "event_id": "e9",
                    "order_id": 2,
                    "customer_id": 2,
                    "status": "paid",
                    "total": "20.50",
                    "event_time": later,
                }
            )
            + "\n"
        )
    _run_once(spark, src, target, ck)
    _run_once(spark, src, target, ck)  # nothing new, table unchanged
    df = spark.read.parquet(str(target))
    assert df.count() == 2
    assert df.filter(F.col("order_id") == 2).first().status == "paid"


def test_hourly_counts_one_row_per_status(spark, tmp_path: Path):
    src = tmp_path / "events"
    counts_target = tmp_path / "status_counts"
    ck = tmp_path / "ck-counts"
    write_events_fixture(str(src), with_duplicate=True)
    q = start_hourly_counts(spark, str(src), str(counts_target), str(ck), available_now=True)
    q.awaitTermination()
    rows = {r.status: r.change_count for r in spark.read.parquet(str(counts_target)).collect()}
    assert rows == {"pending_payment": 2, "paid": 1}


def test_hourly_counts_replay_does_not_double_count(spark, tmp_path: Path):
    src = tmp_path / "events"
    counts_target = tmp_path / "status_counts"
    ck = tmp_path / "ck-counts"
    write_events_fixture(str(src), with_duplicate=True)
    for _ in range(2):
        q = start_hourly_counts(spark, str(src), str(counts_target), str(ck), available_now=True)
        q.awaitTermination()
    df = spark.read.parquet(str(counts_target))
    assert df.count() == 2
    assert df.filter(F.col("status") == "pending_payment").first().change_count == 2


def test_hourly_counts_cover_every_status_in_the_fixture(spark, tmp_path: Path):
    src = tmp_path / "events"
    counts_target = tmp_path / "status_counts"
    ck = tmp_path / "ck-counts"
    write_status_change_fixture(str(src), hours=4, per_hour=3)
    q = start_hourly_counts(spark, str(src), str(counts_target), str(ck), available_now=True)
    q.awaitTermination()
    rows = spark.read.parquet(str(counts_target)).collect()
    assert {r.status for r in rows} == {"pending_payment", "paid", "shipped"}
    assert sum(r.change_count for r in rows) == 12
