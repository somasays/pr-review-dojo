"""Hidden tests for the design, refactor, and test findings in exercise 15.

Fast, no IO: ast and inspect only.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.api.routers import reports
from app.services.rate_limiter import seconds_until_reset


def test_rate_limit_report_has_a_pure_format_function() -> None:
    """DS-21: formatting must be separable from the orchestration that reads the limiter."""
    names = [name for name in dir(reports) if name.startswith(("format_", "render_"))]
    assert names, "expected a format_* or render_* helper in app/api/routers/reports.py"
    fn = getattr(reports, names[0])
    # Callable with plain values, no request, no session, no limiter.
    row = fn("key-a", 3, 100, 42)
    assert row["key"] == "key-a"
    assert row["usage"].endswith("%")


def test_seconds_until_reset_takes_now_as_a_parameter() -> None:
    """DS-09: the clock must not be read inside a function meant to be pure."""
    params = inspect.signature(seconds_until_reset).parameters
    assert "now" in params
    assert seconds_until_reset(1_000.0, 60, now=1_000.0) == 60
    assert seconds_until_reset(1_000.0, 60, now=1_090.0) == 0


def test_reports_router_does_not_read_settings_directly() -> None:
    """DS-10 (refactor): the handler already has the limit through the limiter's policy."""
    source = Path("app/api/routers/reports.py").read_text()
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "get_settings"
    ]
    assert calls == []
