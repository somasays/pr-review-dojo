"""Hidden tests for exercise 13."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pyspark.sql import functions as F

import app.jobs.order_events_stream as m
from app.jobs.fixtures import write_paid_events_fixture
from app.jobs.schemas import ORDER_EVENTS_SCHEMA


def _counts(spark, target: Path) -> dict[int, int]:
    return {r.customer_id: r.paid_count for r in spark.read.parquet(str(target)).collect()}


def _paid(event_id: str, order_id: int, customer_id: int, when: datetime) -> dict:
    return {
        "event_id": event_id,
        "order_id": order_id,
        "customer_id": customer_id,
        "status": "paid",
        "total": "10.50",
        "event_time": when.isoformat(),
    }


def _write_events(src: Path, name: str, rows: list[dict]) -> None:
    src.mkdir(parents=True, exist_ok=True)
    (src / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _run_counts(spark, src: Path, target: Path, ck: Path):
    q = m.start_paid_counts(spark, str(src), str(target), str(ck), available_now=True)
    q.awaitTermination()
    return q


def test_replayed_batch_does_not_double_count(spark, tmp_path: Path):
    """SS-02: foreachBatch can be replayed, so the second sink write must be idempotent."""
    src, target = tmp_path / "events", tmp_path / "paid_counts"
    write_paid_events_fixture(str(src))
    batch = (
        spark.read.schema(ORDER_EVENTS_SCHEMA)
        .json(str(src))
        .filter(F.col("status") == m.PAID_STATUS)
    )
    m.merge_paid_counts(batch, 4, str(target))
    m.merge_paid_counts(batch, 4, str(target))
    assert _counts(spark, target) == {1: 2, 2: 1}


def test_paid_reader_limits_files_per_trigger(spark, tmp_path: Path):
    """SS-08: a backlog must be split across micro-batches, not read in one."""
    src, target, ck = tmp_path / "events", tmp_path / "paid_counts", tmp_path / "ck"
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    for i in range(25):
        _write_events(src, f"events-{i:04d}.json", [_paid(f"b{i}", i + 1, 1, base)])
    q = _run_counts(spark, src, target, ck)
    assert q.lastProgress["batchId"] >= 2


def test_paid_dedupe_state_expires_with_the_watermark(spark, tmp_path: Path):
    """SS-09: dropDuplicates on an id column alone never releases state."""
    src, target, ck = tmp_path / "events", tmp_path / "paid_counts", tmp_path / "ck"
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    q = None
    for i in range(3):
        when = base + timedelta(hours=i)
        _write_events(
            src,
            f"events-{i:04d}.json",
            [_paid(f"h{i}a", 10 * i + 1, 1, when), _paid(f"h{i}b", 10 * i + 2, 2, when)],
        )
        q = _run_counts(spark, src, target, ck)
    assert q is not None
    state = q.lastProgress["stateOperators"][0]
    assert state["numRowsTotal"] < 6


def test_paid_events_uses_the_module_watermark(spark, tmp_path: Path, monkeypatch):
    """SS-17: both readers must move together when the watermark changes."""
    src = tmp_path / "events"
    write_paid_events_fixture(str(src))
    monkeypatch.setattr(m, "WATERMARK", "7 minutes")
    plan = m.paid_events(spark, str(src))._jdf.queryExecution().analyzed().toString()
    assert "7 minutes" in plan


def test_paid_counts_query_has_a_name(spark, tmp_path: Path):
    """SS-18: the query needs a stable name in the metrics and the UI."""
    src, target, ck = tmp_path / "events", tmp_path / "paid_counts", tmp_path / "ck"
    write_paid_events_fixture(str(src))
    q = m.start_paid_counts(spark, str(src), str(target), str(ck), available_now=True)
    try:
        assert q.name == "paid_order_counts"
    finally:
        q.stop()


def test_paid_counts_trigger_is_not_faster_than_the_upsert_trigger():
    """SS-14: a one second trigger lists the source directory once per second."""
    assert m.PAID_COUNTS_TRIGGER == m.UPSERT_TRIGGER
