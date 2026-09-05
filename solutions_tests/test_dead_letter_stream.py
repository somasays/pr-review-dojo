"""Hidden tests for exercise 30."""

import ast
import inspect
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pyspark.sql import functions as F
from pyspark.sql.types import StructField, StructType

import app.jobs.order_events_stream as mod
from app.jobs.fixtures import write_events_fixture
from app.jobs.schemas import ORDER_EVENTS_RAW_SCHEMA
from app.services.notification import InMemorySender, NotificationService

BASE = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
# createDataFrame enforces nullability that the JSON reader does not.
NULLABLE_RAW = StructType(
    [StructField(f.name, f.dataType, True) for f in ORDER_EVENTS_RAW_SCHEMA.fields]
)


def _event(**over):
    e = {
        "event_id": "x1",
        "order_id": 7,
        "customer_id": 1,
        "status": "paid",
        "total": "10.50",
        "event_time": BASE.isoformat(),
    }
    e.update(over)
    return e


def _write(src: Path, name: str, lines: list[str]) -> None:
    src.mkdir(parents=True, exist_ok=True)
    (src / name).write_text("\n".join(lines) + "\n")


def _await(q):
    q.awaitTermination()
    return q


def _notifier():
    return NotificationService(InMemorySender())


# SS-XC and TR-01: the upsert path and the dead letter path must agree on what is
# malformed, and the shipped test never exercised this, only the unparseable line.
def test_event_with_a_null_required_field_is_parked_not_merged(spark, tmp_path: Path):
    src = tmp_path / "events"
    target, dlq = tmp_path / "orders_latest", tmp_path / "dead_letter"
    _write(
        src,
        "events-0001.json",
        [
            json.dumps(_event(event_id="ok1", order_id=1)),
            json.dumps(_event(event_id="bad1", order_id=None)),
            '{"event_id": "trunc", "order_id": 4, "status": "shi',
        ],
    )
    _await(mod.start(spark, str(src), str(target), str(tmp_path / "ck-up"), available_now=True))
    _await(
        mod.start_dead_letter(
            spark, str(src), str(dlq), str(tmp_path / "ck-dl"), _notifier(), available_now=True
        )
    )
    merged = spark.read.parquet(str(target))
    assert merged.filter(F.col("order_id").isNull()).count() == 0
    assert {r.order_id for r in merged.collect()} == {1}
    parked = spark.read.parquet(str(dlq))
    ids = {r.event_id for r in parked.collect()}
    assert "bad1" in ids
    assert parked.count() == 2


# SS-05: a failed batch must not be committed.
def test_a_failing_merge_fails_the_query(spark, tmp_path: Path, monkeypatch):
    src, target, ck = tmp_path / "events", tmp_path / "orders_latest", tmp_path / "ck"
    write_events_fixture(str(src))
    real = mod.latest_per_order

    def boom(events):
        raise RuntimeError("transient object store error")

    monkeypatch.setattr(mod, "latest_per_order", boom)
    q = mod.start(spark, str(src), str(target), str(ck), available_now=True)
    with pytest.raises(Exception):
        q.awaitTermination()
    monkeypatch.setattr(mod, "latest_per_order", real)
    _await(mod.start(spark, str(src), str(target), str(ck), available_now=True))
    assert spark.read.parquet(str(target)).count() == 2


# SS-06: the ops alert must be idempotent and must not fire before the write.
def test_dead_letter_alert_is_keyed_by_batch_and_sent_after_the_write(spark, tmp_path: Path):
    dlq = tmp_path / "dead_letter"
    seen: list[tuple[str, bool]] = []

    class Recorder:
        def send(self, message):
            seen.append((message.dedupe_key, os.path.exists(str(dlq))))

    from app.services.config import Settings

    notifier = NotificationService(Recorder(), Settings(notify_retries=1))
    rows = [
        ("ok1", 1, 1, "paid", None, BASE, None),
        (None, None, None, None, None, None, '{"event_id": "trunc"'),
    ]
    batch = spark.createDataFrame(rows, NULLABLE_RAW)
    mod.dead_letter_batch(batch, 7, str(dlq), notifier)
    mod.dead_letter_batch(batch, 7, str(dlq), notifier)
    assert len(seen) == 2
    assert seen[0][0] == seen[1][0], "a replayed batch must reuse the same dedupe key"
    assert "7" in seen[0][0]
    assert seen[0][1], "the alert fired before the dead letter table was written"
    assert spark.read.parquet(str(dlq)).count() == 1


# SS-11: the dead letter reader must use the declared schema.
def test_dead_letter_reader_keeps_the_declared_types(spark, tmp_path: Path):
    src = tmp_path / "events"
    write_events_fixture(str(src), with_malformed=True)
    types = dict(mod.dead_letter_source(spark, str(src)).dtypes)
    assert types["total"] == "decimal(12,2)"
    assert types["event_time"] == "timestamp"
    assert types["order_id"] == "int"
    assert spark.conf.get("spark.sql.streaming.schemaInference", "false") == "false"


# SS-12: changing the dedupe key must not break a restart from an existing checkpoint.
def test_restart_over_an_existing_checkpoint_still_runs(spark, tmp_path: Path):
    from app.jobs.schemas import ORDER_EVENTS_SCHEMA

    src, target, ck = tmp_path / "events", tmp_path / "orders_latest", tmp_path / "ck"
    write_events_fixture(str(src))
    old = (
        spark.readStream.schema(ORDER_EVENTS_SCHEMA)
        .option("maxFilesPerTrigger", 10)
        .json(str(src))
        .withWatermark("event_time", "10 minutes")
        .dropDuplicatesWithinWatermark(["event_id"])
    )
    _await(
        old.writeStream.option("checkpointLocation", str(ck))
        .foreachBatch(lambda df, bid: None)
        .trigger(availableNow=True)
        .start()
    )
    _await(mod.start(spark, str(src), str(target), str(ck), available_now=True))
    assert spark.read.parquet(str(target)).count() == 2


# SS-01: the hourly counts must expire old windows.
def test_hourly_counts_expire_old_windows(spark, tmp_path: Path):
    src, counts, ck = tmp_path / "events", tmp_path / "counts", tmp_path / "ck"
    progress = None
    for i, hour in enumerate([12, 13, 14, 15]):
        _write(
            src,
            f"events-{i:04d}.json",
            [
                json.dumps(
                    _event(
                        event_id=f"h{hour}",
                        order_id=hour,
                        event_time=BASE.replace(hour=hour).isoformat(),
                    )
                )
            ],
        )
        q = _await(
            mod.start_status_counts(spark, str(src), str(counts), str(ck), available_now=True)
        )
        progress = q.lastProgress
    assert progress is not None
    assert "1970" not in progress["eventTime"]["watermark"]
    assert progress["stateOperators"][0]["numRowsTotal"] < 4


# DS-04: the dead letter query must be handed its notifier, not default to a concrete
# sender deep inside the job, so the caller controls which one is used.
def test_dead_letter_query_requires_a_notifier():
    sig = inspect.signature(mod.start_dead_letter)
    assert sig.parameters["notifier"].default is inspect.Parameter.empty


# DS-08: the counts write should reuse the existing retry helper instead of a
# hand-rolled loop.
def test_counts_write_reuses_the_shared_retry_helper():
    tree = ast.parse(inspect.getsource(mod))
    write_counts_fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "write_counts"
    )
    calls = {
        n.func.id
        for n in ast.walk(write_counts_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "retry" in calls
    assert not hasattr(mod, "_write_counts_with_retry")


# DS-10: settings must be read once when the query starts, not on every micro-batch.
def test_env_setting_is_read_once_not_per_batch():
    tree = ast.parse(inspect.getsource(mod))
    all_calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_settings"
    ]
    assert len(all_calls) <= 1
    write_counts_fn = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "write_counts"
    )
    inner_calls = [
        n
        for n in ast.walk(write_counts_fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_settings"
    ]
    assert inner_calls == []


# Refactor (DS-20): the three start_* functions should share one trigger helper
# instead of repeating the same if/else three times.
def test_start_functions_share_a_trigger_helper():
    tree = ast.parse(inspect.getsource(mod))
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ["start", "start_dead_letter", "start_status_counts"]:
        trigger_attrs = [
            n for n in ast.walk(funcs[name]) if isinstance(n, ast.Attribute) and n.attr == "trigger"
        ]
        assert not trigger_attrs, f"{name} should delegate trigger selection to a shared helper"
