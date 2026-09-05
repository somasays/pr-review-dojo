"""Hidden tests for exercise 10: the shipping-transit design and refactor findings."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.services.order_service import OrderService


def test_transit_days_does_not_import_the_orm():
    """DS-06: app/domain must stay free of app.db, app.services, app.api imports."""
    source = Path("app/domain/shipping.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("app.db"), node.module
            assert not node.module.startswith("app.services"), node.module
            assert not node.module.startswith("app.api"), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("app.db"), alias.name
                assert not alias.name.startswith("app.services"), alias.name
                assert not alias.name.startswith("app.api"), alias.name


def test_transit_days_reuses_daterange():
    """DS-08 (refactor): the day count should not hand-roll what DateRange already does."""
    source = Path("app/domain/shipping.py").read_text()
    tree = ast.parse(source)
    calls_daterange = any(
        isinstance(node, ast.Name) and node.id == "DateRange" for node in ast.walk(tree)
    )
    has_manual_loop = any(isinstance(node, ast.While) for node in ast.walk(tree))
    assert calls_daterange, "transit_days should build a DateRange instead of looping by hand"
    assert not has_manual_loop, "a hand-rolled day-count loop duplicates DateRange"


def test_ship_takes_a_now_parameter():
    """DS-09: ship() must not call the clock internally so tests can pin the time."""
    params = inspect.signature(OrderService.ship).parameters
    assert "now" in params
    # Defaulting to None (falling back to utcnow internally) keeps existing
    # callers working while letting a test pin the timestamp; exercised end
    # to end in tests/test_api_order_shipping.py.
    assert params["now"].default is None
