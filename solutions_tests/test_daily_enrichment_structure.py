"""Shape of the module after the rewrite: a pure transform, no god function,
no flag parameters, and paths carried by LakePaths instead of raw strings."""

import ast
import inspect
from datetime import date
from decimal import Decimal

from app.domain.dates import DateRange
from app.jobs import daily_enrichment as mod
from app.jobs.daily_orders import LakePaths, read_orders
from app.jobs.schemas import CUSTOMER_ENRICHMENT_SCHEMA

MAX_FUNCTION_LINES = 35
SOURCE = inspect.getsource(mod)
TREE = ast.parse(SOURCE)
FUNCTIONS = [n for n in ast.walk(TREE) if isinstance(n, ast.FunctionDef)]


def test_pure_transform_exists_and_takes_only_a_dataframe(spark, lake):
    """The aggregation is callable with a DataFrame, no session and no paths."""
    fn = getattr(mod, "enrich_daily", None)
    assert fn is not None, "expected a pure DataFrame-in, DataFrame-out transform"
    params = list(inspect.signature(fn).parameters)
    assert len(params) == 1, f"expected one parameter, got {params}"

    orders = read_orders(spark, LakePaths(lake), DateRange.single(date(2026, 8, 1)))
    out = fn(orders)
    assert [f.name for f in out.schema.fields] == [
        f.name for f in CUSTOMER_ENRICHMENT_SCHEMA.fields
    ]
    row = out.filter("customer_id = 3").collect()[0]
    assert row.paid_total == Decimal("91.00")
    assert row.avg_order_value == Decimal("45.50")


def test_no_function_is_a_god_function():
    long = {
        f.name: f.end_lineno - f.lineno
        for f in FUNCTIONS
        if f.end_lineno and f.end_lineno - f.lineno > MAX_FUNCTION_LINES
    }
    assert not long, f"functions longer than {MAX_FUNCTION_LINES} lines: {long}"


def test_withcolumn_chain_is_gone():
    calls = SOURCE.count(".withColumn(") + SOURCE.count(".withColumnRenamed(")
    assert calls <= 1, f"{calls} withColumn calls remain"


def test_no_boolean_flag_parameters():
    flags = [
        (f.name, arg.arg)
        for f in FUNCTIONS
        for arg, default in zip(
            f.args.args[len(f.args.args) - len(f.args.defaults) :], f.args.defaults, strict=True
        )
        if isinstance(default, ast.Constant) and isinstance(default.value, bool)
    ] + [
        (f.name, arg.arg)
        for f in FUNCTIONS
        for arg, default in zip(f.args.kwonlyargs, f.args.kw_defaults, strict=True)
        if isinstance(default, ast.Constant) and isinstance(default.value, bool)
    ]
    assert not flags, f"boolean flag parameters remain: {flags}"


def test_paths_come_from_lakepaths_not_raw_strings():
    roots = [(f.name, a.arg) for f in FUNCTIONS for a in f.args.args if a.arg == "root"]
    assert not roots, f"functions still take a raw root string: {roots}"
    assert "LakePaths" in SOURCE, "expected the existing LakePaths helper to be reused"
    assert '"{root}/' not in SOURCE and "'{root}/" not in SOURCE, "path built by hand"
