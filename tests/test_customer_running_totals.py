from pathlib import Path

from app.jobs.fixtures import write_paid_events_fixture
from app.jobs.order_events_stream import start_customer_totals


def _totals(spark, target: Path) -> dict[int, float]:
    return {r.customer_id: float(r.total_paid) for r in spark.read.parquet(str(target)).collect()}


def test_customer_totals_stream_writes_one_row_per_customer(spark, tmp_path: Path):
    src, target, ck = tmp_path / "events", tmp_path / "customer_totals", tmp_path / "ck_totals"
    write_paid_events_fixture(str(src))
    q = start_customer_totals(spark, str(src), str(target), str(ck), available_now=True)
    q.awaitTermination()
    assert _totals(spark, target) == {1: 31.0, 2: 30.5}
