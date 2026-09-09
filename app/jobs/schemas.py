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

PAYMENT_EVENTS_SCHEMA = StructType(
    [
        StructField("event_id", StringType(), False),
        StructField("payment_id", StringType(), False),
        StructField("order_id", IntegerType(), False),
        StructField("status", StringType(), False),
        StructField("amount", DecimalType(12, 2), True),
        StructField("event_time", TimestampType(), False),
    ]
)

PAYMENT_BATCH_METRICS_SCHEMA = StructType(
    [
        StructField("batch_id", IntegerType(), False),
        StructField("event_count", IntegerType(), False),
        StructField("failed_count", IntegerType(), False),
        StructField("captured_amount", DecimalType(14, 2), False),
    ]
)

PAYMENT_ALERTS_SCHEMA = StructType(
    [
        StructField("batch_id", IntegerType(), False),
        StructField("reason", StringType(), False),
        StructField("failed_count", IntegerType(), False),
        StructField("event_count", IntegerType(), False),
    ]
)
