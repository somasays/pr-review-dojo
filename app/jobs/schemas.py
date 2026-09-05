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

CORRUPT_COLUMN = "_corrupt_record"

# The JSON reader ignores the non-nullable flags above, so a well formed line
# with a null required field parses into a null column rather than being
# rejected. The extra column carries the raw text of a line that failed to
# parse; see malformed() in order_events_stream for the routing rule.
ORDER_EVENTS_RAW_SCHEMA = StructType(
    [*ORDER_EVENTS_SCHEMA.fields, StructField(CORRUPT_COLUMN, StringType(), True)]
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
