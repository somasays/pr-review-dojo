from pathlib import Path

from app.jobs.fixtures import write_payment_events_fixture
from app.jobs.payment_events_stream import start


def _run_once(spark, src: Path, root: Path, ck: Path) -> None:
    q = start(spark, str(src), str(root), str(ck), available_now=True)
    q.awaitTermination()


def test_latest_status_per_payment(spark, tmp_path: Path):
    src, root, ck = tmp_path / "events", tmp_path / "lake", tmp_path / "ck"
    write_payment_events_fixture(str(src))
    _run_once(spark, src, root, ck)
    rows = {r.payment_id: r.status for r in spark.read.parquet(f"{root}/payments_latest").collect()}
    assert rows == {"p1": "captured", "p2": "failed", "p3": "failed", "p4": "captured"}
