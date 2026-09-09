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

CUSTOMER_ENRICHMENT_SCHEMA = StructType(
    [
        StructField("customer_id", IntegerType(), False),
        StructField("order_count", IntegerType(), False),
        StructField("paid_total", DecimalType(14, 2), False),
        StructField("avg_order_value", DecimalType(14, 2), False),
        StructField("large_order_count", IntegerType(), False),
        StructField("first_order_hour", IntegerType(), False),
        StructField("last_order_hour", IntegerType(), False),
        StructField("dt", StringType(), False),
    ]
)
