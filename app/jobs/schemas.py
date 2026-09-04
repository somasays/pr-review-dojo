"""Schemas for lake tables. Readers always pass an explicit schema."""

from __future__ import annotations

from pyspark.sql.types import (
    DecimalType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

ORDERS_SCHEMA = StructType(
    [
        StructField("order_id", IntegerType(), False),
        StructField("customer_id", IntegerType(), False),
        StructField("status", StringType(), False),
        StructField("currency", StringType(), False),
        StructField("total", DecimalType(12, 2), False),
        StructField("created_at", TimestampType(), False),
        StructField("dt", StringType(), False),
    ]
)

ORDER_EVENTS_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), False),
        StructField("order_id", IntegerType(), False),
        StructField("customer_id", IntegerType(), False),
        StructField("status", StringType(), False),
        StructField("total", DecimalType(12, 2), True),
        StructField("event_time", TimestampType(), False),
    ]
)

DAILY_CUSTOMER_SCHEMA = StructType(
    [
        StructField("customer_id", IntegerType(), False),
        StructField("order_count", IntegerType(), False),
        StructField("paid_total", DecimalType(14, 2), False),
        StructField("cancelled_count", IntegerType(), False),
        StructField("dt", StringType(), False),
    ]
)

PRODUCTS_SCHEMA = StructType(
    [
        StructField("sku", StringType(), False),
        StructField("name", StringType(), False),
        StructField("category", StringType(), False),
        StructField("unit_price", DecimalType(12, 2), False),
        StructField("effective_date", StringType(), False),
    ]
)

ORDER_LINES_SCHEMA = StructType(
    [
        StructField("order_id", IntegerType(), False),
        StructField("sku", StringType(), False),
        StructField("quantity", IntegerType(), False),
        StructField("status", StringType(), False),
        StructField("line_total", DecimalType(12, 2), False),
        StructField("dt", StringType(), False),
    ]
)

DAILY_PRODUCT_SCHEMA = StructType(
    [
        StructField("sku", StringType(), False),
        StructField("product_name", StringType(), True),
        StructField("category_label", StringType(), True),
        StructField("units_sold", IntegerType(), False),
        StructField("revenue", DecimalType(14, 2), False),
        StructField("avg_line_value", DecimalType(14, 2), True),
        StructField("dt", StringType(), False),
    ]
)
