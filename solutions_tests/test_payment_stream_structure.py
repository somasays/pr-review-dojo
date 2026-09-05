"""The seams the rewrite is supposed to create."""

import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.jobs import payment_events_stream as m
from app.jobs.schemas import PAYMENT_EVENTS_SCHEMA

MAX_FUNCTION_LINES = 30


def test_pure_helpers_are_module_level():
    for name in ("parse_events", "latest_per_payment", "alert_reason", "write_batch"):
        assert callable(getattr(m, name, None)), f"{name} is not importable from the module"


def test_alert_reason_is_callable_without_spark():
    assert m.alert_reason(4, 2) == "high_failure_rate"
    assert m.alert_reason(4, 1) is None
    assert m.alert_reason(2, 2) is None


def test_no_function_is_longer_than_the_limit():
    tree = ast.parse(Path(m.__file__).read_text())
    too_long = [
        (n.name, n.end_lineno - n.lineno + 1)
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and (n.end_lineno or 0) - n.lineno + 1 > MAX_FUNCTION_LINES
    ]
    assert too_long == []


def test_latest_per_payment_runs_on_a_static_dataframe(spark):
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    df = spark.createDataFrame(
        [
            ("a1", "p1", 1, "authorized", Decimal("10.50"), base),
            ("a2", "p1", 1, "captured", Decimal("10.50"), base + timedelta(minutes=1)),
            ("a3", "p2", 2, "failed", Decimal("3.00"), base),
        ],
        PAYMENT_EVENTS_SCHEMA,
    )
    out = {r.payment_id: r.status for r in m.latest_per_payment(df).collect()}
    assert out == {"p1": "captured", "p2": "failed"}
