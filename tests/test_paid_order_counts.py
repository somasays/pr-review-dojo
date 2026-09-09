from pathlib import Path

from app.jobs.fixtures import write_paid_events_fixture
from app.jobs.order_events_stream import batch_paid_counts, start_paid_counts


def _counts(spark, target: Path) -> dict[int, int]:
    return {r.customer_id: r.paid_count for r in spark.read.parquet(str(target)).collect()}


def test_batch_paid_counts_counts_each_order_once(spark, tmp_path: Path):
    src = tmp_path / "events"
    write_paid_events_fixture(str(src))
    events = spark.read.json(str(src)).filter("status = 'paid'")
    rows = {r.customer_id: r.paid_count for r in batch_paid_counts(events).collect()}
    assert rows == {1: 2, 2: 1}


def test_paid_counts_stream_writes_one_row_per_customer(spark, tmp_path: Path):
    src, target, ck = tmp_path / "events", tmp_path / "paid_counts", tmp_path / "ck_counts"
    write_paid_events_fixture(str(src))
    q = start_paid_counts(spark, str(src), str(target), str(ck), available_now=True)
    q.awaitTermination()
    assert _counts(spark, target) == {1: 2, 2: 1}
