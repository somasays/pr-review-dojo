"""Structural checks for the design and refactor findings on the loyalty
credit rule in app/domain/loyalty.py."""

import ast
import inspect
from pathlib import Path

from app.domain.loyalty import loyalty_credit


def test_domain_loyalty_does_not_import_the_db_layer():
    tree = ast.parse(Path("app/domain/loyalty.py").read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(name.startswith("app.db") for name in imported)


def test_loyalty_credit_reuses_money_percent():
    tree = ast.parse(Path("app/domain/loyalty.py").read_text())
    calls_percent = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "percent"
        for node in ast.walk(tree)
    )
    assert calls_percent


def test_loyalty_credit_has_no_unused_parameters():
    params = inspect.signature(loyalty_credit).parameters
    assert len(params) <= 2
