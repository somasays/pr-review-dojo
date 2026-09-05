"""Behavior of the payment events stream, pinned before and after the rewrite."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.jobs.fixtures import write_payment_events_fixture
from app.jobs.payment_events_stream import start


def _run_once(spark, src: Path, root: Path, ck: Path) -> None:
    q = start(spark, str(src), str(root), str(ck), available_now=True)
    q.awaitTermination()


def _rows(spark, path: str) -> list:
    return spark.read.parquet(path).collect()


def test_first_batch_merges_dedupes_counts_and_alerts(spark, tmp_path: Path):
    src, root, ck = tmp_path / "events", tmp_path / "lake", tmp_path / "ck"
    write_payment_events_fixture(str(src))
    _run_once(spark, src, root, ck)

    latest = {r.payment_id: r.status for r in _rows(spark, f"{root}/payments_latest")}
    assert latest == {"p1": "captured", "p2": "failed", "p3": "failed", "p4": "captured"}

    metrics = _rows(spark, f"{root}/payment_batch_metrics")
    assert len(metrics) == 1
    assert metrics[0].event_count == 4
    assert metrics[0].failed_count == 2
    assert metrics[0].captured_amount == Decimal("110.50")

    alerts = _rows(spark, f"{root}/payment_alerts")
    assert [(a.reason, a.failed_count, a.event_count) for a in alerts] == [
        ("high_failure_rate", 2, 4)
    ]


def test_second_batch_upserts_appends_metrics_and_stays_quiet(spark, tmp_path: Path):
    src, root, ck = tmp_path / "events", tmp_path / "lake", tmp_path / "ck"
    write_payment_events_fixture(str(src))
    _run_once(spark, src, root, ck)

    later = (datetime(2026, 8, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=9)).isoformat()
    (src / "payments-0002.json").write_text(
        json.dumps(
            {
                "event_id": "pe9",
                "payment_id": "p2",
                "order_id": 2,
                "status": "captured",
                "amount": "20.00",
                "event_time": later,
            }
        )
        + "\n"
    )
    _run_once(spark, src, root, ck)
    _run_once(spark, src, root, ck)  # nothing new, tables unchanged

    latest = {r.payment_id: r.status for r in _rows(spark, f"{root}/payments_latest")}
    assert latest == {"p1": "captured", "p2": "captured", "p3": "failed", "p4": "captured"}

    metrics = sorted(_rows(spark, f"{root}/payment_batch_metrics"), key=lambda r: r.batch_id)
    assert [(m.event_count, m.failed_count) for m in metrics] == [(4, 2), (1, 0)]
    assert len(_rows(spark, f"{root}/payment_alerts")) == 1
