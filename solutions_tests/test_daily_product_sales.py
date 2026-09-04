"""Hidden tests for exercise 14."""

import inspect
from datetime import date
from decimal import Decimal

from chispa import assert_df_equality

from app.domain.dates import DateRange
from app.jobs import daily_orders
from app.jobs.daily_orders import (
    LakePaths,
    aggregate_by_product,
    read_order_lines,
    read_products,
    run,
    run_backfill,
)
from app.jobs.fixtures import write_products_fixture
from app.jobs.schemas import DAILY_PRODUCT_SCHEMA, ORDER_LINES_SCHEMA

DAY = "2026-08-01"


def _lake(spark, tmp_path, lines, products, name="lake"):
    root = str(tmp_path / name)
    (
        spark.createDataFrame(lines, ORDER_LINES_SCHEMA)
        .write.mode("overwrite")
        .partitionBy("dt")
        .parquet(f"{root}/order_lines")
    )
    write_products_fixture(spark, root, products)
    return root


def _product_rows(spark, root, days=None):
    paths = LakePaths(root)
    days = days or DateRange.single(date(2026, 8, 1))
    lines = read_order_lines(spark, paths, days)
    return aggregate_by_product(lines, read_products(spark, paths))


def test_money_columns_stay_decimal(spark, tmp_path):
    """SB-06: revenue and the average must not go through a double."""
    products = [("GIZMO", "Gizmo", "electronics", Decimal("0.07"), "2026-01-01")]
    lines = [
        (1, "GIZMO", 1, "paid", Decimal("0.07"), DAY),
        (2, "GIZMO", 1, "paid", Decimal("0.07"), DAY),
        (3, "GIZMO", 1, "paid", Decimal("0.07"), DAY),
    ]
    root = _lake(spark, tmp_path, lines, products)
    actual = _product_rows(spark, root)
    expected = spark.createDataFrame(
        [("GIZMO", "Gizmo", "Electronics", 3, Decimal("0.21"), Decimal("0.07"), DAY)],
        DAILY_PRODUCT_SCHEMA,
    )
    assert_df_equality(actual, expected, ignore_nullable=True)


def test_read_products_uses_an_explicit_schema(spark, tmp_path):
    """SB-12: the dimension read declares its columns and their types."""
    from app.jobs.schemas import PRODUCTS_SCHEMA

    root = str(tmp_path / "erp")
    (
        spark.createDataFrame(
            [("00123", "Widget", "home_office", Decimal("19.99"), "2026-01-01", "erp")],
            "sku string, name string, category string, "
            "unit_price decimal(12,2), effective_date string, source_system string",
        )
        .write.mode("overwrite")
        .parquet(f"{root}/products")
    )
    df = read_products(spark, LakePaths(root))
    assert [(f.name, f.dataType) for f in df.schema] == [
        (f.name, f.dataType) for f in PRODUCTS_SCHEMA
    ]
    row = df.collect()[0]
    assert row.sku == "00123"
    assert row.unit_price == Decimal("19.99")


def test_backfill_rerun_does_not_duplicate_rows(spark, lake):
    """SB-03 and TR-01: rerunning a backfill range must not double the rows."""
    paths = LakePaths(lake)
    days = DateRange(date(2026, 8, 1), date(2026, 8, 3))
    run_backfill(spark, paths, days)
    first = spark.read.parquet(paths.daily_product_sales).count()
    run_backfill(spark, paths, days)
    second = spark.read.parquet(paths.daily_product_sales).count()
    assert second == first


def test_backfill_range_read_is_pruned(spark):
    """SB-07: the backfill path must not wrap dt in a function that blocks pruning."""
    source = inspect.getsource(daily_orders)
    assert "to_date" not in source
    backfill_source = inspect.getsource(run_backfill)
    assert "read_order_lines(" in backfill_source


def test_backfill_is_a_separate_function_not_a_flag(spark):
    """DS-11: run must not switch behavior on a boolean flag."""
    sig = inspect.signature(run)
    assert "backfill" not in sig.parameters
    assert not any(p.annotation is bool for p in sig.parameters.values())
    assert callable(run_backfill)
    assert run_backfill is not run


def test_backfill_takes_a_date_range_not_raw_dates(spark):
    """DS-13: the backfill reader must reuse DateRange, not raw start/end dates."""
    assert not hasattr(daily_orders, "read_order_lines_range")
    params = list(inspect.signature(run_backfill).parameters.values())
    assert sum(1 for p in params if p.annotation is date) == 0


def test_backfill_chunks_with_date_range_split(spark):
    """Refactor (DS-08): chunking should reuse DateRange.split, not a hand-rolled loop."""
    source = inspect.getsource(run_backfill)
    assert "while " not in source
    assert ".split(" in source


def test_daily_product_sales_matches_orders_status_filter(spark, lake):
    """Guard: only paid, shipped and delivered lines contribute revenue."""
    df = _product_rows(spark, lake)
    total = df.agg({"revenue": "sum"}).collect()[0][0]
    assert total == Decimal("122.00")
