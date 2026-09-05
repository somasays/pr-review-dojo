"""Hidden tests for exercise 13."""

import ast
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path

from pyspark.sql import functions as F

import app.jobs.order_events_stream as m
from app.jobs.fixtures import write_paid_events_fixture
from app.jobs.schemas import ORDER_EVENTS_SCHEMA


def _counts(spark, target: Path) -> dict[int, int]:
    return {r.customer_id: r.paid_count for r in spark.read.parquet(str(target)).collect()}


def _totals(spark, target: Path) -> dict[int, float]:
    return {r.customer_id: float(r.total_paid) for r in spark.read.parquet(str(target)).collect()}


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


def test_paid_counts_trigger_is_not_faster_than_the_upsert_trigger():
    """SS-14: a one second trigger lists the source directory once per second."""
    assert m.PAID_COUNTS_TRIGGER == m.UPSERT_TRIGGER


def test_customer_totals_do_not_grow_unbounded_state(spark, tmp_path: Path):
    """SS-10: a new customer every batch must not leave a streaming aggregate's state
    growing without bound; the fix merges per-batch deltas into a plain table instead
    of a complete-mode groupBy aggregate."""
    src, target, ck = tmp_path / "events", tmp_path / "customer_totals", tmp_path / "ck"
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    q = None
    for i in range(4):
        _write_events(src, f"events-{i:04d}.json", [_paid(f"c{i}", i + 1, i + 1, base)])
        q = m.start_customer_totals(spark, str(src), str(target), str(ck), available_now=True)
        q.awaitTermination()
    assert q is not None
    # A complete-mode groupBy adds its own state operator (stateStoreSave) on top of
    # the reader's dedupe; a foreachBatch merge over a non-aggregated stream keeps
    # only the dedupe operator.
    assert len(q.lastProgress["stateOperators"]) == 1
    assert _totals(spark, target) == {1: 10.5, 2: 10.5, 3: 10.5, 4: 10.5}


def test_batch_paid_counts_uses_count_distinct(spark, tmp_path: Path):
    """DS-08: counting distinct orders by hand (distinct + groupBy + count) reimplements
    the countDistinct aggregate function that does the same job in one step."""
    src = inspect.getsource(m.batch_paid_counts)
    tree = ast.parse(src)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "countDistinct" in calls
    assert "distinct" not in calls


def test_batch_writers_share_one_staging_helper(spark, tmp_path: Path):
    """DS-20: upsert_batch and merge_paid_counts duplicated the stage-then-overwrite
    write; both should call one shared helper instead of repeating it."""
    upsert_src = inspect.getsource(m.upsert_batch)
    counts_src = inspect.getsource(m.merge_paid_counts)
    assert "_stage_and_overwrite" in upsert_src
    assert "_stage_and_overwrite" in counts_src
    # Neither function does its own staging write anymore.
    assert "__staging" not in upsert_src
    assert "__staging" not in counts_src


def test_start_paid_counts_takes_a_paths_object_not_four_strings():
    """DS-13 (refactor): source_dir, target, and checkpoint travel together across
    start, start_paid_counts, and start_customer_totals; grouping them removes the
    repetition, worth doing but not blocking."""
    params = inspect.signature(m.start_paid_counts).parameters
    assert len(params) <= 3
