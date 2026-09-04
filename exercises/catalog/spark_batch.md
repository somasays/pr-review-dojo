# Spark batch defect catalog

Source material for `/exercise` and `/seed` when the domain is `spark-batch`. Each entry describes
one defect that a feature PR against `app/jobs/daily_orders.py` (and the files around it) can
carry, how to plant it so the diff reads as honest work from a mid-level engineer, and what the
hidden test under `solutions_tests/` asserts. The base job is already correct: `read_orders`
filters `dt` with `isin(days.partition_keys())` against an explicit `ORDERS_SCHEMA` and a
`basePath`, `aggregate_daily` keeps money in `DecimalType`, `write_daily` repartitions by `dt` and
relies on `spark.sql.sources.partitionOverwriteMode=dynamic` from `get_spark` so a rerun replaces
only the days it computed, and `partitionColumnTypeInference` is off so `dt` stays a string. So
every plant lives in new feature code (a weekly customer summary, a product level aggregate joined
to a products dimension, a region breakdown joined to a customers dimension, a CSV extract for
finance, a `--backfill` flag that loops over days) or in a "cleanup" of the existing reader and
writer. Hidden tests reuse the session scoped `spark` fixture and the `lake` fixture from
`conftest.py` (three days of orders written by `write_orders_fixture`), plus `tmp_path`, `chispa`,
and plan inspection, so each runs in seconds on `local[*]`. Every exercise also plants one entry
from "Looks wrong but is fine"; flagging it costs the reviewer a false positive.

## Defects

### SB-01: Partition overwrite switched to static, so a one day rerun deletes the whole table
- Severity: Blocker
- Description: `spark.sql.sources.partitionOverwriteMode` is set to `static` (or the config line is
  dropped from `get_spark`), so `write_daily`'s `mode("overwrite")` clears every `dt` directory
  under `daily_customer_orders` and leaves only the day just computed.
- Planting: A `--backfill` flag is added to `main`, and the author hits the Spark warning about
  dynamic partition overwrite being unsupported on some catalogs. The fix in `spark_session.py`
  reads `.config("spark.sql.sources.partitionOverwriteMode", "static")` with the comment
  "explicit is better, we always pass the partition we want". The comment in `write_daily` still
  says "only the partitions present in df are replaced", so the writer looks untouched.
- Hidden test: Using the `lake` fixture, run for 2026-08-01 through 2026-08-03, then rerun for
  2026-08-02 alone. Assert the distinct `dt` values under `daily_customer_orders` are still all
  three days and that the 2026-08-01 partition has 3 rows. Also assert
  `spark.conf.get("spark.sql.sources.partitionOverwriteMode") == "dynamic"`.

### SB-02: Weekly customer summary reads the orders table with no partition filter
- Severity: Blocker
- Description: The new `weekly_summary` calls `spark.read.parquet(paths.orders)` and applies the
  date range after the aggregation, so every run scans every `dt` ever written instead of the
  seven partitions it needs.
- Planting: Add `weekly_summary(spark, paths, days)` next to `aggregate_daily` for a rolling seven
  day report. It reads the table directly rather than through `read_orders`, groups by
  `customer_id`, then does `.filter(F.col("dt") >= start_key)` on the aggregate. The PR says the
  filter "is applied on the smaller side for speed", which sounds like an optimization.
- Hidden test: Call `weekly_summary` for 2026-08-02 through 2026-08-03 on the `lake` fixture and
  assert every path in `df.inputFiles()` contains `dt=2026-08-02` or `dt=2026-08-03`, so the
  2026-08-01 partition is never opened. Also assert the string from `df.explain(True)` contains a
  non empty `PartitionFilters` entry naming `dt`.

### SB-03: `write_daily` switched to append, so a rerun doubles every row
- Severity: Blocker
- Description: The writer uses `mode("append")`, so re-running a day adds a second set of rows for
  the same `(customer_id, dt)` and every downstream sum for that day is doubled.
- Planting: The `--backfill` loop fails on the second day with a path conflict while the author is
  testing against a local directory, and switching to `mode("append")` makes it pass. The diff is
  one word in `write_daily`, and the PR notes "append is safe because each run writes a different
  dt". README convention 3 ("batch jobs overwrite only the partitions they compute") is not
  mentioned.
- Hidden test: Run for 2026-08-01 twice on the same `lake` root and assert the
  `daily_customer_orders` partition for that day has exactly 3 rows and that `paid_total` for
  customer 1 is `Decimal("10.50")`, compared with chispa. On the planted code the counts and the
  totals double.

### SB-04: Duplicate rows in the products dimension multiply order rows in the join
- Severity: Blocker
- Description: The products dimension carries one row per `(sku, effective_date)`, so joining
  orders to it on `sku` emits one order row per dimension version, and `order_count` and
  `paid_total` are inflated for every product that has ever been repriced.
- Planting: A product level aggregate is added: `read_products` loads a `products` table from the
  lake and `aggregate_by_product` joins it to the order lines before grouping. The fixture writer
  gains `write_products_fixture` with a single row per sku, so nothing in `tests/` catches it; the
  production dimension is the one that has history. The join reads
  `orders.join(products, "sku", "left")` with no dedupe or "current row" filter.
- Hidden test: Write a products fixture where one sku has two rows (an old price and a current
  one), run the product aggregate, and assert the total order count equals the number of order
  rows read, not more, and that `paid_total` matches the value computed without the join, asserted
  with `assert_df_equality`.

### SB-05: Full result pulled to the driver with `toPandas()` to write the finance extract
- Severity: Blocker
- Description: The CSV extract calls `daily.toPandas()` (or `.collect()`) on the whole aggregate
  and writes it from the driver, so the driver holds every customer row for the range in memory
  and the job dies on a real month instead of a three day fixture.
- Planting: Finance wants a single CSV file rather than a directory of parts. The author adds
  `write_csv_extract(df, path)` that does `df.toPandas().to_csv(path, index=False)` with the
  comment "one file, no part-00000 names to explain". It works on every fixture, and pandas is
  already installed as a test dependency.
- Hidden test: Monkeypatch `pyspark.sql.DataFrame.toPandas` and `.collect` to raise, run the job
  including the extract on the `lake` fixture, and assert it completes and that the extract path
  contains the expected rows when read back. The reference fix uses
  `df.coalesce(1).write.mode("overwrite").option("header", True).csv(path)`.

### SB-06: Money aggregated as double, so cents drift
- Severity: Blocker
- Description: A new average order value column casts `total` to `double` before summing and
  dividing, so `paid_total` and `avg_order_value` are binary floats and no longer match the
  `Numeric(12, 2)` values in the database or the `DecimalType` in `DAILY_CUSTOMER_SCHEMA`.
- Planting: `F.avg` on a decimal returns a decimal with a scale the author finds ugly in the
  output, so `aggregate_daily` gains `F.avg(F.col("total").cast("double")).alias(
  "avg_order_value")` and `F.sum(paid.cast("double"))`, then a `round(..., 2)` "to make it tidy".
  The PR describes it as a formatting change. README convention 2 ("Decimal for money") is the
  rule being broken.
- Hidden test: Write an orders fixture whose totals are `0.07`, `0.07`, and `0.07` for one
  customer, run the aggregate, and assert `paid_total` is exactly `Decimal("0.21")` and that the
  output field type is `DecimalType(14, 2)` and the average column is a `DecimalType`, not
  `DoubleType`, compared with chispa on the full DataFrame.

### SB-07: `to_date` applied to the partition column, so nothing is pruned
- Severity: Major
- Description: The range filter becomes `F.to_date(F.col("dt"), "yyyy-MM-dd").between(start, end)`,
  which wraps the partition column in a function the file source cannot push into
  `PartitionFilters`, so Spark lists and reads every partition and filters afterwards.
- Planting: `read_orders` is "generalized" for the backfill flag: instead of building the key list
  from `days.partition_keys()`, the author compares dates directly so that a long range does not
  produce a long `IN` list. The comment "Read only the partitions in the range. Never a full scan."
  stays, and the row counts in `tests/test_daily_orders.py` are unchanged, so the suite is green.
- Hidden test: Call `read_orders` for 2026-08-02 on the `lake` fixture and assert every entry of
  `df.inputFiles()` is under `dt=2026-08-02`, and that the `df.explain(True)` string shows `dt`
  inside `PartitionFilters` rather than only in a `Filter` node above the scan.

### SB-08: Inner join to the customers dimension silently drops orders
- Severity: Major
- Description: The region breakdown uses an inner join to the customers dimension, so orders whose
  `customer_id` is not in that table (new customers, deleted accounts, the guest checkout id) are
  dropped and the day's revenue is quietly lower than `aggregate_daily` reports.
- Planting: A `region` column is requested on the daily table. The author adds `read_customers` and
  `daily.join(customers, "customer_id")`, which defaults to an inner join, and validates against a
  fixture where the dimension covers every customer. The PR says "region comes from the customers
  table, which is the source of truth for customers".
- Hidden test: Write a customers dimension that omits customer 3, run the job, and assert the
  output still has 3 customers for 2026-08-01 with the sum of `paid_total` unchanged, and that the
  missing customer's `region` is null or `"unknown"` rather than the row being absent.

### SB-09: Filter on the dimension column after a left join turns it back into an inner join
- Severity: Major
- Description: The job left joins the customers dimension and then filters
  `F.col("region") != "INTERNAL"` to exclude test accounts. Null compares to nothing in SQL, so
  every unmatched customer, exactly the rows the left join was meant to keep, is dropped again.
- Planting: The left join in SB-08's feature is correct, and this is the next line: a request to
  exclude internal accounts from the report. The author writes the filter after the join because
  "the region column only exists after the join". A reviewer sees `how="left"` and stops looking.
- Hidden test: With a customers dimension that omits customer 3 and marks customer 2 as
  `INTERNAL`, assert the output contains customer 1 and customer 3 and excludes customer 2. On the
  planted code customer 3 disappears. The reference fix filters the dimension before the join or
  uses `F.coalesce(F.col("region"), F.lit("unknown")) != "INTERNAL"`.

### SB-10: Hot key skew with broadcast disabled and no salting
- Severity: Major
- Description: The guest checkout customer id accounts for most rows on a busy day, and the
  dimension join is forced to a sort merge join by
  `spark.sql.autoBroadcastJoinThreshold=-1`, so one reducer task receives every guest order and the
  stage runs for as long as that single task takes.
- Planting: While debugging a flaky local run the author hits
  `SparkException: Could not execute broadcast` on a slow machine and adds
  `.config("spark.sql.autoBroadcastJoinThreshold", "-1")` to `get_spark` with the comment
  "broadcast timeouts in CI". Adaptive skew handling is also turned off in the same commit
  (`spark.sql.adaptive.enabled=false`) because "AQE changed the number of output files".
- Hidden test: Assert `spark.conf.get("spark.sql.autoBroadcastJoinThreshold")` is not `-1` and that
  `spark.conf.get("spark.sql.adaptive.enabled")` is `"true"`, then assert the executed plan string
  for the dimension join contains `BroadcastHashJoin` and not `SortMergeJoin` for a dimension of a
  few rows.

### SB-11: `cache()` placed before the partition filter, so the whole table is materialized
- Severity: Major
- Description: `read_orders` caches the DataFrame it gets from `spark.read.parquet` and then
  filters on `dt`, so the cached relation is the entire orders table and the pruning happens above
  the cache instead of below it.
- Planting: The backfill loop reads the same table once per day, so the author "reads it once and
  caches it". In `read_orders` the chain becomes `.parquet(paths.orders).cache().filter(...)`,
  which reads as a small reordering. On the three day fixture it is fast and correct.
- Hidden test: Call `read_orders` for one day on the `lake` fixture and assert the executed plan
  has no `InMemoryTableScan` above a scan whose `PartitionFilters` are empty. Simpler and just as
  decisive: assert `df.inputFiles()` covers only the requested day, and that after the call
  `spark.sparkContext._jsc.sc().getRDDStorageInfo()` is empty because nothing unfiltered was
  cached.

### SB-12: New dimension read without an explicit schema
- Severity: Major
- Description: `read_products` uses `spark.read.option("inferSchema", True).csv(...)` (or plain
  `.parquet` with no `.schema`), so `unit_price` becomes a double, `sku` codes with leading zeros
  lose them, and the type of every column depends on whichever file the sampler happened to read.
- Planting: The products extract lands as a CSV drop from the ERP, so the author reaches for
  `inferSchema` rather than adding a `PRODUCTS_SCHEMA` to `app/jobs/schemas.py`, noting "the
  header names are already right". The module docstring in `schemas.py` still says "Readers always
  pass an explicit schema".
- Hidden test: Assert `read_products(spark, paths).schema` equals a `PRODUCTS_SCHEMA` constant
  exported from `app.jobs.schemas`, with `unit_price` as `DecimalType(12, 2)` and `sku` as
  `StringType`. Feed a fixture with sku `"00123"` and a price of `19.99` and assert both survive
  the read exactly.

### SB-13: Repartition by a high cardinality key before the write
- Severity: Major
- Description: `write_daily` repartitions by `customer_id` instead of `dt`, so the output has one
  file per customer per partition directory, which on a real day is tens of thousands of tiny
  parquet files and a listing cost that dominates every downstream read.
- Planting: The author reads that repartitioning by the join key speeds up the customers join and
  carries the same key into the writer: `df.repartition("customer_id").write...partitionBy("dt")`.
  The PR says "co-locates each customer's rows", which sounds like a locality win.
- Hidden test: Run the job for the full three day range, then count `part-*.parquet` files under
  each `dt=` directory of `daily_customer_orders` and assert each holds at most a small fixed
  number (2 is enough on the fixture). Also assert `daily.rdd.getNumPartitions()` is not equal to
  the distinct customer count.

### SB-14: Day bucketing derived from `created_at` in local time
- Severity: Major
- Description: A backfill path recomputes `dt` from the timestamp with
  `F.date_format(F.from_utc_timestamp("created_at", "America/Los_Angeles"), "yyyy-MM-dd")`, so
  orders placed in the last seven hours of a UTC day are written into the previous partition and
  are counted twice when the neighboring day is rerun.
- Planting: A stakeholder says the daily numbers "look shifted" against a Pacific time dashboard,
  and the author fixes the report rather than the dashboard. The alternative plant is a
  `.config("spark.sql.session.timeZone", "America/Los_Angeles")` line in `get_spark`, replacing the
  `UTC` one, with the comment "match the business day".
- Hidden test: Assert `spark.conf.get("spark.sql.session.timeZone") == "UTC"`, then run the job on
  the `lake` fixture (whose orders sit at 00:00 through 05:00 UTC) and assert the set of `dt`
  values written equals the set of `dt` values in the source partitions, with the same per day row
  counts. On the planted code the early hours move to the previous day.

### SB-15: `mergeSchema` enabled on the orders read
- Severity: Minor
- Description: `read_orders` adds `.option("mergeSchema", "true")`, so Spark reads the footer of
  every file in the table (not only the partitions being scanned) at planning time, and the
  resulting schema is whatever the union of historical files happens to be rather than
  `ORDERS_SCHEMA`.
- Planting: A column is added to the orders writer in the same PR, and an older partition fails to
  read during local testing, so `mergeSchema` is switched on as the quick unblock and left in.
  The explicit `.schema(ORDERS_SCHEMA)` call stays right above it, which makes the option look
  harmless.
- Hidden test: Add a partition to the `lake` fixture written with an extra column, then assert
  `read_orders(...).schema == ORDERS_SCHEMA` and that the planning time read does not open files
  outside the requested partitions, checked through `df.inputFiles()`.

### SB-16: No coalesce on the daily write, one file per shuffle partition
- Severity: Minor
- Description: The aggregate is written straight from the shuffle, so each `dt` directory gets one
  file per shuffle partition (200 in production, 4 in the test session), most of them a few
  kilobytes.
- Planting: A `groupBy` is added for the new grain and the existing `repartition("dt")` is dropped
  as "an unnecessary shuffle before a partitioned write". The local test output looks fine because
  `get_spark` sets `spark.sql.shuffle.partitions` to 4.
- Hidden test: Run the job with `spark.sql.shuffle.partitions` temporarily set higher (16) and
  assert each `dt=` directory of `daily_customer_orders` contains exactly one `part-*.parquet`
  file, which holds only when the writer repartitions or coalesces by `dt` first.

### SB-17: `count()` called for a log line on every run
- Severity: Minor
- Description: `run` logs "wrote %d rows" by calling `daily.count()` before the write, which
  executes the entire read, join, and aggregation once for the count and again for the write.
- Planting: An operations request for run visibility. The author adds
  `log.info("aggregating %d rows", orders.count())` in `run` right after `read_orders`, and a
  second `daily.count()` before `write_daily`. Both look like harmless observability.
- Hidden test: Wrap `pyspark.sql.DataFrame.count` with a counting spy, call `run` on the `lake`
  fixture, and assert it was not called. The reference fix logs the range and lets the writer
  report through Spark metrics, or reuses a count the job already needs.

### SB-18: Cached DataFrame never unpersisted in the backfill loop
- Severity: Minor
- Description: The backfill path caches the orders read once per day inside a loop and never calls
  `unpersist`, so a long backfill accumulates one cached relation per day until executors start
  evicting the blocks that are actually being used.
- Planting: `main` grows `for chunk in days.split(7): run(spark, paths, chunk)`, and `run` gains
  `orders.cache()` because the DataFrame feeds two aggregates. There is no `finally` and no
  `unpersist`, which is easy to miss because a single run behaves correctly.
- Hidden test: Run the job over the full three day range through the chunked path and assert
  `spark.sparkContext._jsc.sc().getRDDStorageInfo()` is empty when `run` returns, so every cached
  relation was released.

### SB-19: Python UDF where a Spark builtin exists
- Severity: Minor
- Description: A `@F.udf(StringType())` wraps `lambda s: s.upper()` for the status label and
  another formats the `dt` string, so every row crosses the Python boundary and the filter on
  status can no longer be pushed into the scan.
- Planting: The report needs display friendly status labels. The author writes
  `status_label = F.udf(lambda s: s.replace("_", " ").title())` because the mapping "is easier to
  read as Python", and applies it in `aggregate_daily` before the `groupBy`. Both
  `F.upper` and `F.initcap`/`F.regexp_replace` cover it.
- Hidden test: Assert the executed plan string for the aggregate contains neither `BatchEvalPython`
  nor `PythonUDF`, and compare the labeled output against expected rows with chispa so the fix has
  to preserve behavior.

### SB-20: Product aggregate unioned by position rather than by name
- Severity: Minor
- Description: The customer level and product level results are combined with `union`, which
  matches columns by position, so the day the two `select` lists diverge (a column added in the
  middle, or the `dt` column moved) the values land under the wrong names with no error.
- Planting: `run` returns a single DataFrame for both grains, so the author writes
  `customer_rows.union(product_rows)` after building each with its own `select`. The two selects
  currently agree, so the tests pass and the code reads as symmetric.
- Hidden test: Reorder the columns in one of the two `select` lists inside the test (or build the
  second grain from a DataFrame whose columns are in a different order) and assert the combined
  output still has `paid_total` values that are decimals matching the per grain results, which
  only holds with `unionByName`.

### SB-21: `cache()` on a DataFrame used once
- Severity: Nit
- Description: The products dimension is cached immediately after being read and is then consumed
  by a single join, so the cache costs a materialization and an entry in the cache manager and
  saves nothing.
- Planting: The author caches the dimension "because it is small and reused", but the second use
  never lands in this PR. One line in `read_products`.
- Hidden test: Assert the executed plan for the join contains no `InMemoryTableScan` and that
  `spark.sparkContext._jsc.sc().getRDDStorageInfo()` is empty after the aggregate runs.

### SB-22: Lake sub-path hard-coded instead of added to `LakePaths`
- Severity: Nit
- Description: The products dimension path is written as `f"{paths.root}/products"` at three call
  sites rather than as a property on `LakePaths`, so the next path change has to find all three.
- Planting: `read_products`, the fixture writer, and the CLI each build the string inline. The
  dataclass with `orders` and `daily_customer_orders` properties sits directly above, which is
  what makes it a Nit rather than an argument.
- Hidden test: Assert `LakePaths("/lake").products == "/lake/products"` and that
  `app/jobs/daily_orders.py` contains no literal `"/products"`.

### SB-23: Output column names diverge from `DAILY_CUSTOMER_SCHEMA`
- Severity: Nit
- Description: The new columns are aliased `total` and `n_orders` while the schema constant in
  `app/jobs/schemas.py` names them `paid_total` and `order_count`, so writes match by position and
  any reader that passes the schema constant gets mislabeled columns.
- Planting: The author extends `aggregate_daily` and picks shorter aliases in the new `agg` block,
  updating the assertions in `tests/test_daily_orders.py` to match rather than the schema.
- Hidden test: Assert the field names of the written `daily_customer_orders` table equal the field
  names of `DAILY_CUSTOMER_SCHEMA` in the same order.

## Looks wrong but is fine

### SB-CLEAN-01: `.option("basePath", paths.orders)` on a read that already points at that path
- Pattern: `read_orders` sets `basePath` to the same directory it then passes to `.parquet(...)`.
- Why it is fine: `basePath` is what tells Spark where the partition columns start, so `dt` is
  recovered as a column rather than being lost or inferred from a deeper path. It also keeps the
  read correct if a later change passes explicit partition directories to `.parquet()`. Dropping
  it makes `dt` disappear from the schema and breaks `groupBy("customer_id", "dt")`.
- What a reviewer might wrongly say: "`basePath` is redundant when you read the table root, delete
  it."

### SB-CLEAN-02: `dt` typed as `StringType` in `ORDERS_SCHEMA` and compared with `isin`
- Pattern: The partition column is a string and the filter is
  `F.col("dt").isin(days.partition_keys())` rather than a date comparison.
- Why it is fine: The lake stores `dt` as a `YYYY-MM-DD` string by design, README "Data layout"
  says so, and `get_spark` sets `partitionColumnTypeInference.enabled=false` to keep it that way.
  Lexicographic order on that format is calendar order, and an `IN` list of literal keys is exactly
  what the file source pushes into `PartitionFilters`. A `to_date` cast is the defect in SB-07.
- What a reviewer might wrongly say: "Dates should not be strings; parse `dt` to a date and use
  `between` so ranges work."

### SB-CLEAN-03: `repartition("dt")` right before a `partitionBy("dt")` write
- Pattern: `write_daily` shuffles by `dt` immediately before writing partitioned by `dt`.
- Why it is fine: Without it each shuffle partition may contain rows for several days, and every
  task then writes a file into every `dt` directory, which is the small files problem in SB-16.
  Repartitioning by the same column the write partitions on gives one writer per day and one file
  per day. The cost is one shuffle of an already aggregated, tiny DataFrame.
- What a reviewer might wrongly say: "`partitionBy` already groups by `dt`; this shuffle is dead
  work, remove it."

### SB-CLEAN-04: `F.count("order_id")` rather than `F.count("*")`
- Pattern: `aggregate_daily` counts a named column instead of all rows.
- Why it is fine: `order_id` is declared non nullable in `ORDERS_SCHEMA`, so the two counts are
  equal on any row this reader can produce, and naming the column documents the grain (orders, not
  rows of some later join). Should a join ever add rows with a null `order_id`, counting the column
  is the behavior the report wants.
- What a reviewer might wrongly say: "`count` on a column skips nulls, so this undercounts orders;
  use `count('*')`."

### SB-CLEAN-05: `F.lit(0).cast("decimal(12,2)")` in the `otherwise` branch
- Pattern: The `paid` expression returns a cast zero literal for non paid statuses instead of a
  plain `0` or a null.
- Why it is fine: `when`/`otherwise` unify the branch types, and an integer literal would promote
  the whole expression away from `DecimalType(12, 2)`, which then feeds
  `sum(...).cast("decimal(14,2)")` and the money contract in README convention 2. Using null
  instead would work for `sum` but would produce null rather than `0.00` for a customer whose
  orders were all cancelled, which the schema declares non nullable.
- What a reviewer might wrongly say: "The cast on a zero literal is noise, `F.lit(0)` is enough."
