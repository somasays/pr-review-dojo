"""Hidden tests for exercise 22."""

import json
from datetime import UTC, datetime
from pathlib import Path

from pyspark.sql import functions as F

import app.jobs.order_events_stream as oes
from app.jobs.fixtures import write_events_fixture, write_status_change_fixture
from app.jobs.order_events_stream import start, start_hourly_counts


def _write_many(src: Path, count: int) -> None:
    src.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    for i in range(count):
        event = {
            "event_id": f"m{i}",
            "order_id": 500 + i,
            "customer_id": (i % 3) + 1,
            "status": "paid",
            "total": "5.00",
            "event_time": base.isoformat(),
        }
        (src / f"events-{i:04d}.json").write_text(json.dumps(event) + "\n")


def _nonempty_batches(query) -> int:
    return len([p for p in query.recentProgress if p["numInputRows"] > 0])


def _write_event(path: Path, event_id: str, order_id: int, minute: int) -> None:
    path.write_text(
        json.dumps(
            {
                "event_id": event_id,
                "order_id": order_id,
                "customer_id": 1,
                "status": "paid",
                "total": "10.50",
                "event_time": datetime(2026, 8, 1, 12, minute, tzinfo=UTC).isoformat(),
            }
        )
        + "\n"
    )


def _dropped_duplicates(query) -> int:
    return sum(
        op["customMetrics"].get("numDroppedDuplicateRows", 0)
        for progress in query.recentProgress
        for op in progress["stateOperators"]
    )


def test_retried_event_is_still_deduped_minutes_later(spark, tmp_path: Path):
    """SS-04: a ten second watermark expires dedupe state before a retry arrives."""
    src, target, ck = tmp_path / "events", tmp_path / "orders_latest", tmp_path / "ck"
    write_events_fixture(str(src), with_duplicate=False)
    start(spark, str(src), str(target), str(ck), available_now=True).awaitTermination()
    # A later event advances the watermark past the retry window of a short watermark.
    _write_event(src / "events-0002.json", "e4", 4, 5)
    start(spark, str(src), str(target), str(ck), available_now=True).awaitTermination()
    # The producer re-sends the paid event for order 1, which it already sent at 12:01.
    _write_event(src / "events-0003.json", "e2", 1, 1)
    query = start(spark, str(src), str(target), str(ck), available_now=True)
    query.awaitTermination()
    assert _dropped_duplicates(query) == 1


def test_counts_query_does_not_share_the_upsert_checkpoint(spark, tmp_path: Path):
    """SS-03: two queries on one checkpoint make the second resume the first's offsets."""
    src, target = tmp_path / "events", tmp_path / "orders_latest"
    counts_target, ck = tmp_path / "status_counts", tmp_path / "ck"
    write_events_fixture(str(src))
    start(spark, str(src), str(target), str(ck), available_now=True).awaitTermination()
    start_hourly_counts(
        spark, str(src), str(counts_target), str(ck), available_now=True
    ).awaitTermination()
    rows = spark.read.parquet(str(counts_target)).collect()
    assert sum(r.change_count for r in rows) == 3


def test_counts_window_is_built_on_event_time(spark, tmp_path: Path):
    """SS-07: windowing on current_timestamp buckets by run time, not by event time."""
    src = tmp_path / "events"
    counts_target, ck = tmp_path / "status_counts", tmp_path / "ck"
    write_status_change_fixture(str(src), hours=4, per_hour=3)
    start_hourly_counts(
        spark, str(src), str(counts_target), str(ck), available_now=True
    ).awaitTermination()
    # date_format renders in the session time zone (UTC), unlike the driver-local
    # conversion PySpark applies to a bare timestamp column.
    rendered = spark.read.parquet(str(counts_target)).select(
        F.date_format("window_start", "yyyy-MM-dd HH:mm").alias("w")
    )
    assert sorted({r.w for r in rendered.collect()}) == [
        "2026-08-01 12:00",
        "2026-08-01 13:00",
        "2026-08-01 14:00",
        "2026-08-01 15:00",
    ]


def test_counts_reader_limits_files_per_trigger(spark, tmp_path: Path):
    """SS-08: without maxFilesPerTrigger the whole backlog lands in one micro-batch."""
    src = tmp_path / "events"
    counts_target, ck = tmp_path / "status_counts", tmp_path / "ck"
    _write_many(src, 25)
    query = start_hourly_counts(spark, str(src), str(counts_target), str(ck), available_now=True)
    query.awaitTermination()
    assert _nonempty_batches(query) >= 3


def test_latest_row_helpers_share_one_ranking_function(spark, tmp_path: Path):
    """DS-20: latest_per_order and latest_per_window both rank-and-filter by
    hand; they should call one shared helper instead of duplicating it."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(oes))
    calls_in = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
            "latest_per_order",
            "latest_per_window",
        ):
            calls_in[node.name] = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    shared = calls_in["latest_per_order"] & calls_in["latest_per_window"]
    assert shared, "latest_per_order and latest_per_window should share a helper call"
    for name in shared:
        assert hasattr(oes, name)


def test_batch_merges_share_one_write_helper(spark, tmp_path: Path):
    """DS-08: upsert_batch and merge_counts_batch both stage-then-overwrite
    the target by hand; they should call one shared write helper instead."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(oes))
    calls_in = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
            "upsert_batch",
            "merge_counts_batch",
        ):
            calls_in[node.name] = {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    shared = calls_in["upsert_batch"] & calls_in["merge_counts_batch"]
    assert shared, "upsert_batch and merge_counts_batch should share a write helper"


def test_counts_report_four_distinct_hourly_windows(spark, tmp_path: Path):
    """TR-01: the shipped test only checked totals, which does not tell a
    per-run window apart from a per-event-hour window. Assert the window
    count directly, the way the improved shipped test does."""
    src = tmp_path / "events"
    counts_target, ck = tmp_path / "status_counts", tmp_path / "ck"
    write_status_change_fixture(str(src), hours=4, per_hour=3)
    start_hourly_counts(
        spark, str(src), str(counts_target), str(ck), available_now=True
    ).awaitTermination()
    windows = spark.read.parquet(str(counts_target)).select("window_start").distinct().count()
    assert windows == 4
