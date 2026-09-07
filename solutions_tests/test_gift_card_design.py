"""Structural checks for the design and refactor findings on gift card
redemption."""

import ast
import inspect
from pathlib import Path

from app.domain.gift_card import redeem
from app.services.order_service import OrderService


def test_gift_card_router_has_no_sqlalchemy_query():
    tree = ast.parse(Path("app/api/routers/gift_cards.py").read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name.startswith("sqlalchemy") for name in imported)


def test_apply_gift_card_takes_a_single_money_argument():
    params = list(inspect.signature(OrderService._apply_gift_card).parameters.values())
    # self, code, total: no separate amount/currency primitives to keep in sync.
    assert [p.name for p in params] == ["self", "code", "total"]
    assert "Money" in str(params[2].annotation)


def test_redeem_has_no_unused_parameters():
    params = inspect.signature(redeem).parameters
    assert len(params) == 2
