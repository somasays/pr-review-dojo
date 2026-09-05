"""SparkSession factory.

Local mode for tests and development. Production submits with its own
master, so nothing here should assume local[*] beyond the defaults.
"""

from __future__ import annotations

import os
import sys

from pyspark.sql import SparkSession


def get_spark(app_name: str = "dojo") -> SparkSession:
    # Workers must run the same interpreter as the driver.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    return (
        SparkSession.builder.master(os.environ.get("SPARK_MASTER", "local[*]"))
        .appName(app_name)
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "4")
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        # dt partition keys stay strings; never let Spark infer them as dates.
        .config("spark.sql.sources.partitionColumnTypeInference.enabled", "false")
        # Needed by the dead letter reader, which has no schema of its own.
        .config("spark.sql.streaming.schemaInference", "true")
        .getOrCreate()
    )
