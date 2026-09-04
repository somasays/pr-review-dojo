"""Hidden test: the payments router asks the repository, it does not query directly."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "app" / "api" / "routers" / "payments.py"


def test_router_module_has_no_sqlalchemy_import():
    tree = ast.parse(SOURCE.read_text())
    names = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "sqlalchemy" not in names
