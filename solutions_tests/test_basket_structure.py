"""Structural expectations for the rewrite. These fail on the exercise branch."""

from __future__ import annotations

import ast
import inspect

from app.api.routers import orders as orders_router
from app.services.config import Settings
from app.services.notification import InMemorySender, NotificationService
from app.services.order_service import OrderService

MAX_HANDLER_LINES = 30


def _router_tree() -> ast.Module:
    return ast.parse(inspect.getsource(orders_router))


def test_pricing_and_reorder_live_in_the_order_service():
    for name in ("preview", "reorder"):
        assert hasattr(OrderService, name), f"OrderService is missing {name}"
    signature = inspect.signature(OrderService.preview)
    assert list(signature.parameters) == ["self", "customer_id", "items", "discount_codes"]


def test_the_reorder_email_lives_in_the_notification_service():
    sender = InMemorySender()
    NotificationService(sender, Settings()).order_reordered("ada@example.com", 8, 7, "37.52")

    assert len(sender.sent) == 1
    assert sender.sent[0].subject == "Order 8 placed from order 7"
    assert sender.sent[0].body == "We are preparing the same items again. Your total is 37.52."
    assert sender.sent[0].dedupe_key == "order-reordered:8"


def test_the_router_builds_no_queries_of_its_own():
    tree = _router_tree()
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "text" not in calls, "the orders router still holds raw SQL"
    assert "select" not in calls, "the orders router still builds select statements"
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not any(m.startswith("sqlalchemy") for m in imported), (
        "the orders router imports sqlalchemy directly"
    )


def test_every_handler_fits_on_one_screen():
    tree = _router_tree()
    too_long = {
        node.name: node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.end_lineno is not None
        and node.end_lineno - node.lineno + 1 > MAX_HANDLER_LINES
    }
    assert too_long == {}, f"handlers longer than {MAX_HANDLER_LINES} lines: {too_long}"


def test_the_two_handlers_do_not_repeat_each_other():
    tree = _router_tree()
    bodies = {
        node.name: ast.dump(ast.Module(body=node.body, type_ignores=[]))
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in {"preview_basket", "reorder"}
    }
    assert set(bodies) == {"preview_basket", "reorder"}
    for name, dumped in bodies.items():
        assert dumped.count("DISCOUNT_CODES") == 0, f"{name} re-derives discount selection"
        assert dumped.count("TAX_RATES") == 0, f"{name} re-derives the tax rate"
