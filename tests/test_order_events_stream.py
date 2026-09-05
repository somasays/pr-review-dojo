import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pyspark.sql import functions as F

from app.jobs.fixtures import write_events_fixture
from app.jobs.order_events_stream import start, start_dead_letter, start_status_counts
from app.services.notification import InMemorySender, NotificationService


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


def test_malformed_line_is_parked_in_the_dead_letter_table(spark, tmp_path: Path):
    src, dlq, ck = tmp_path / "events", tmp_path / "dead_letter", tmp_path / "ck-dl"
    write_events_fixture(str(src), with_malformed=True)
    notifier = NotificationService(InMemorySender())
    q = start_dead_letter(spark, str(src), str(dlq), str(ck), notifier, available_now=True)
    q.awaitTermination()
    rows = spark.read.parquet(str(dlq)).collect()
    assert len(rows) == 1
    assert rows[0]._corrupt_record.startswith('{"event_id": "e4"')


def test_hourly_counts_bucket_events_by_event_time(spark, tmp_path: Path):
    src, counts, ck = tmp_path / "events", tmp_path / "counts", tmp_path / "ck-counts"
    write_events_fixture(str(src), with_duplicate=False)
    q = start_status_counts(spark, str(src), str(counts), str(ck), available_now=True)
    q.awaitTermination()
    rows = spark.read.parquet(str(counts)).collect()
    by_status = {r.status: r["count"] for r in rows}
    assert by_status == {"pending_payment": 2, "paid": 1}
    assert len({r.window_start for r in rows}) == 1
    assert {r.window_end - r.window_start for r in rows} == {timedelta(hours=1)}
