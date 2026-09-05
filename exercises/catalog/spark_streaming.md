# Spark streaming defect catalog

Source material for `/exercise` and `/seed` when the domain is `spark-streaming`. Each entry
describes one defect that a feature PR against `app/jobs/order_events_stream.py` (and the files
around it) can carry, how to plant it so the diff reads as honest work from a mid-level engineer,
and what the hidden test under `solutions_tests/` asserts. The base job is already correct:
`read_events` passes `ORDER_EVENTS_SCHEMA`, sets `maxFilesPerTrigger`, watermarks `event_time` by
`WATERMARK`, and dedupes with `dropDuplicatesWithinWatermark`; `upsert_batch` is a full replay-safe
merge; `main` maps `--once` to `availableNow` and calls `awaitTermination`. So every plant lives in
new feature code (a second sink, an hourly window, a per-customer total, a status-change alert) or
in a "cleanup" of the existing reader. Hidden tests reuse the session-scoped `spark` fixture from
`conftest.py`, `write_events_fixture` from `app/jobs/fixtures.py`, a `tmp_path` checkpoint, and
`trigger(availableNow=True)`, so each runs in seconds on `local[*]`. Every exercise also plants one
entry from "Looks wrong but is fine"; flagging it costs the reviewer a false positive.

## Do not plant

- Trivia (a linter's job, not a reviewer's): SS-17, SS-18
- Internals (deeper than a generalist interview goes): SS-09, SS-13, SS-15, SS-16

Everything else is the middle band a strong generalist is expected to reason about. Pick from it.

## Defects

### SS-01: Windowed aggregation with no watermark keeps every window in state forever
- Severity: Blocker
- Description: A new hourly status count uses `groupBy(window(...))` in update mode with no
  `withWatermark`, so no window is ever finalized and the state store grows by one row per
  (hour, status) for the life of the checkpoint.
- Planting: Add `hourly_status_counts(spark, source_dir)` to `order_events_stream.py` for a
  dashboard. It builds its own reader with `spark.readStream.schema(ORDER_EVENTS_SCHEMA).json(src)`
  "because the dedupe in `read_events` is not needed for counts", then
  `groupBy(F.window("event_time", "1 hour"), "status").count()`, written with
  `outputMode("update")` through `foreachBatch`. The author picks update mode after append mode
  raised an AnalysisException about a missing watermark, and moves on.
- Hidden test: Run the query with `availableNow` three times against the same checkpoint, adding a
  file per run whose events sit at 12:00, 13:00, and 14:00 on 2026-08-01. Assert
  `query.lastProgress["eventTime"]["watermark"]` is not the 1970 epoch string and that
  `stateOperators[0]["numRowsTotal"]` after the third run is less than the total number of
  distinct (window, status) pairs seen, which only holds once a watermark expires the 12:00
  window.

### SS-02: Second sink appended inside foreachBatch, so a replayed batch writes its rows twice
- Severity: Blocker
- Description: `upsert_batch` gains a `status_changes` history table written with
  `mode("append")` and no batch_id in the data or the path, so the batch that Spark re-executes
  after a crash between the write and the commit appends the same rows a second time.
- Planting: The feature is an audit trail of every status transition. In `upsert_batch`, after the
  merge, add `incoming.select("order_id", "status", "event_time").write.mode("append")
  .parquet(f"{target}_history")`. The docstring still says "safe to replay" because the author
  reasons the merge above is idempotent, and the history write looks like one more line.
- Hidden test: Build a batch DataFrame from `write_events_fixture` output and call
  `upsert_batch(df, 0, target)` twice with the same `batch_id`. Assert the row count of
  `{target}_history` equals the number of distinct orders in the batch, not double. The reference
  fix writes the history partitioned by `_batch_id` with dynamic overwrite, or filters out rows
  whose `_batch_id` already exists before appending.

### SS-03: Two streaming queries share one checkpoint location
- Severity: Blocker
- Description: The new alert query is started with the same `checkpointLocation` as the upsert
  query, so whichever starts second resumes from the other's committed offsets and silently
  skips every event the first one already processed.
- Planting: Add `start_alerts(spark, source_dir, alerts_target, checkpoint)` for a "shipped
  order" notification stream that filters `status == "shipped"` and writes with `foreachBatch`.
  `main` grows an `--alerts-target` flag and calls both `start` and `start_alerts` with
  `args.checkpoint`. The CLI help text says "checkpoint directory for the job", singular.
- Hidden test: Run `start` with `availableNow` and wait, then run `start_alerts` with the same
  checkpoint path and wait. Assert the alerts table exists and contains the shipped order from a
  second fixture file; on the planted code `lastProgress["numInputRows"]` for the alerts query is
  0 and the table is missing. The fixed code derives distinct sub-paths per query, for example
  `f"{checkpoint}/upsert"` and `f"{checkpoint}/alerts"`.

### SS-04: Watermark cut from 10 minutes to 10 seconds drops real late events
- Severity: Blocker
- Description: `WATERMARK` is lowered to `"10 seconds"` to shrink dedupe state, so any event that
  arrives more than ten seconds behind the newest event already seen (normal for a retried
  producer) is dropped by `dropDuplicatesWithinWatermark` before it reaches the merge.
- Planting: A one-line change in `order_events_stream.py` with the commit message "reduce dedupe
  state retention". The PR description cites the state size on the dashboard and notes that
  "events arrive in order from the producer". No test changes, because the fixture events all
  land in the first micro-batch, where the watermark is still zero.
- Hidden test: Run `start` once on the base fixture (max event_time 12:02), then write
  `events-0002.json` with an event for order 3 at 12:01:30 and run again on the same checkpoint.
  Assert order 3 is present in `orders_latest` and that
  `lastProgress["stateOperators"][0]["numRowsDroppedByWatermark"]` is 0.

### SS-05: Exception in foreachBatch is caught and logged, and the batch is committed anyway
- Severity: Blocker
- Description: `upsert_batch` wraps the merge in `try/except Exception: log.exception(...)`, so a
  failed write returns normally, Spark marks the batch committed, and the events in that batch
  are never processed again.
- Planting: The feature is "make the stream resilient to a transient S3 error". The author wraps
  the body of `upsert_batch` from the `spark.read.parquet(target)` line through the staging
  rewrite in a try block and logs "batch %d failed, will retry next trigger". Nothing retries;
  the next trigger reads the next offsets.
- Hidden test: Monkeypatch `latest_per_order` to raise on the first call, then run `start` with
  `availableNow`. Assert the query raises `StreamingQueryException` (or that the batch is not
  present in `checkpoint/commits`), and that a second run after removing the patch produces the
  expected two rows. On the planted code the first run succeeds and the table is never written.

### SS-06: Shipping notification sent before the merge, keyed by nothing stable
- Severity: Major
- Description: The alert side effect runs on `batch.collect()` before the parquet write and builds
  its `dedupe_key` from `uuid4()`, so a batch that fails after sending and is replayed sends
  every notification again, and `batch_id` is never used to make the send idempotent.
- Planting: In `upsert_batch`, before `latest_per_order(batch)`, loop over
  `batch.filter(F.col("status") == "shipped").collect()` and call
  `NotificationService.send(customer_id, "shipped", dedupe_key=str(uuid4()))`. The author chose
  a random key because "each event is a new notification". Ordering it before the write "so
  customers hear first" looks like a product choice.
- Hidden test: Replace the notifier with a recording fake, call `upsert_batch(df, 7, target)`
  twice, and assert the fake saw one send per shipped order with a `dedupe_key` that contains the
  order id and event id (or the batch id), and that the send happened after the target directory
  existed. The base `NotificationService` dedupe contract is what makes the fix a key change.

### SS-07: Hourly window built on `current_timestamp()` instead of `event_time`
- Severity: Major
- Description: The hourly count windows on `F.current_timestamp()`, so events are bucketed by when
  the micro-batch ran rather than when the status changed; a backfill puts a week of events into
  one window and the watermark on `event_time` no longer bounds the aggregation.
- Planting: In `hourly_status_counts`, `F.window(F.current_timestamp(), "1 hour")` with a comment
  "processing time is what the dashboard shows". The `withWatermark("event_time", WATERMARK)`
  call stays, which makes the query look correct at a glance.
- Hidden test: Run the query on the fixture (events dated 2026-08-01 12:00 to 12:02) and assert
  the single output window starts at `2026-08-01T12:00:00Z`. On the planted code the window start
  is today's hour. Also assert the plan string from `query.explain()` contains `event_time` in
  the window expression.

### SS-08: `maxFilesPerTrigger` dropped from the file source
- Severity: Major
- Description: A reader cleanup removes `.option("maxFilesPerTrigger", 10)`, so the first trigger
  after any outage reads the entire backlog in one micro-batch and the driver runs out of memory
  on the `collect` and window steps in `upsert_batch`.
- Planting: `read_events` is refactored into a `_reader(spark, source_dir)` helper shared by the
  upsert and alert queries, and the option is lost in the move. The diff shows a reorder of
  builder calls, which reads as a no-op tidy.
- Hidden test: Write 25 single-event files into the source directory, run with `availableNow`, and
  assert `query.lastProgress["batchId"] >= 2` (three batches of at most 10 files). On the planted
  code `batchId` is 0.

### SS-09: `dropDuplicates` replaces `dropDuplicatesWithinWatermark`, so dedupe state never expires
- Severity: Major
- Description: Plain `dropDuplicates(["event_id"])` only evicts state when the watermark column is
  part of the subset, so with `event_id` alone every id ever seen stays in the state store.
- Planting: The author swaps the call in `read_events` "for compatibility with the Spark 3.4 image
  in staging" and keeps `withWatermark` in place. Tests still pass because dedupe within one batch
  behaves the same either way.
- Hidden test: Run three `availableNow` batches on one checkpoint with events at 12:00, 13:00, and
  14:00. After the third run assert `stateOperators[0]["numRowsTotal"]` is less than the number of
  distinct event ids written, which requires the 12:00 keys to have expired. On the planted code
  it equals the total.

### SS-10: Per-customer lifetime total in complete output mode over an unbounded key
- Severity: Major
- Description: `customer_running_totals` does `groupBy("customer_id").agg(F.sum("total"))` with
  `outputMode("complete")` into `foreachBatch`, so every micro-batch rewrites all customers ever
  seen and the state store holds one row per customer with no expiry; no watermark can help
  because the key has no time component.
- Planting: The feature is "customer spend so far" for the API reports. The author notes that
  complete mode is required by Spark for a non-windowed aggregate and adds the query next to
  `start`. The foreachBatch writes the whole result with `mode("overwrite")`, which hides the
  cost in a small fixture.
- Hidden test: Run four batches, each with a new customer id, and assert
  `stateOperators[0]["numRowsTotal"]` and `lastProgress["sink"]["numOutputRows"]` do not both
  grow linearly with the batch count. The reference fix drops the streaming aggregate and merges
  per-customer sums inside `upsert_batch` the same way `orders_latest` is merged, keyed by
  `customer_id`.

### SS-11: Streaming JSON source with schema inference instead of `ORDER_EVENTS_SCHEMA`
- Severity: Major
- Description: The alert reader calls `spark.readStream.json(source_dir)` with no `.schema`, and
  `get_spark` gains `spark.sql.streaming.schemaInference=true` to allow it, so `total` is
  inferred as a string, `event_time` as a string, and the schema changes with the first file
  seen after each restart.
- Planting: In `start_alerts`, "the alert only needs status and order_id, so the full schema is
  overkill". The config flip in `spark_session.py` is described as "needed for the alert reader".
  The existing comment about never inferring `dt` sits two lines above it.
- Hidden test: Assert `read_events(spark, src).schema == ORDER_EVENTS_SCHEMA` and that the alert
  query's source DataFrame has `DecimalType(12, 2)` for `total` and `TimestampType` for
  `event_time`. Also assert `spark.conf.get("spark.sql.streaming.schemaInference")` is `"false"`.

### SS-12: Dedupe key changed while the checkpoint path stays the same
- Severity: Major
- Description: The dedupe subset changes from `["event_id"]` to `["order_id", "status"]`, which
  changes the state key schema, so the next restart from the existing checkpoint fails with
  `StateSchemaNotCompatible` and the job stays down until someone clears state by hand.
- Planting: The author wants retried events with fresh ids to dedupe too, edits
  `dropDuplicatesWithinWatermark` in `read_events`, and writes "no migration needed, checkpoint is
  unchanged" in the PR description. `main` keeps the same `--checkpoint` default in the deploy
  script.
- Hidden test: Build the old-key query inline (schema, watermark, `["event_id"]` dedupe, no-op
  foreachBatch) and run it on a checkpoint. Then call `start` on the same base path and assert it
  completes and writes both orders. The fixed code namespaces the checkpoint with a state version
  (`f"{checkpoint}/v{STATE_VERSION}"`) that the PR bumps, so the test passes without touching the
  old directory.

### SS-13: `awaitTermination(timeout=30)` in the backfill path
- Severity: Minor
- Description: The `--once` path waits at most 30 seconds, so a backfill longer than that returns
  from `main` while the query is still running, the process exits, and the run finishes on the
  next invocation instead of this one.
- Planting: `main` is changed to `q.awaitTermination(timeout=30 if args.once else None)` with the
  comment "one-shot runs should not hang CI". The value is copied from a local test.
- Hidden test: Monkeypatch `upsert_batch` to sleep 2 seconds per batch, write enough files for 3
  batches, call `main([... "--once"])` with a patched timeout of 1 second, and assert the target
  contains every order when `main` returns. On the planted code `main` returns early and the
  assertion fails.

### SS-14: Alert query triggers every second on a directory source
- Severity: Minor
- Description: `start_alerts` uses `trigger(processingTime="1 second")`, so the file source lists
  the directory once per second, which on the production object store is a listing call per
  second per query and mostly empty micro-batches.
- Planting: "Alerts should feel instant" in the PR description. The upsert query keeps its
  30 second trigger two lines above, so the difference is visible in the diff.
- Hidden test: Assert the trigger for the alerts query is at least the upsert trigger by reading a
  module-level `ALERT_TRIGGER` constant, or by starting it with a fake writer and inspecting the
  `trigger` call, and that it is not below `"10 seconds"`.

### SS-15: `--once` mapped to `trigger(once=True)` instead of `availableNow`
- Severity: Minor
- Description: `once=True` ignores `maxFilesPerTrigger` and processes the whole backlog in a single
  micro-batch, and it is deprecated, so the backfill path loses the batching that the live path
  keeps.
- Planting: `start` is changed to take `trigger: str` and branch on it; the `"once"` branch calls
  `writer.trigger(once=True)` because "once is the name of the flag". Tests still pass since the
  fixture is a single file.
- Hidden test: Write 25 single-event files, run through the `--once` code path, and assert
  `query.lastProgress["batchId"] >= 2`. With `once=True` it is 0.

### SS-16: Test starts a processingTime query and never stops it
- Severity: Minor
- Description: A new test calls `start(..., available_now=False)`, sleeps, reads the table, and
  returns without `q.stop()`, so the query keeps polling `tmp_path` on the session-scoped
  SparkSession for the rest of the session and its progress events interleave with later tests.
- Planting: `tests/test_order_events_stream.py` gains
  `test_live_trigger_picks_up_new_file` with `time.sleep(35)` after writing a second file, "to
  cover the production trigger". No `try/finally`, no `stop()`.
- Hidden test: After importing and running the tests module, assert `spark.streams.active` is
  empty. The reference fix wraps the query in `try/finally: q.stop()` or drops the sleep in favor
  of `availableNow`.

### SS-17: Watermark literal duplicated instead of reusing `WATERMARK`
- Severity: Nit
- Description: The alert reader hard-codes `"10 minutes"` rather than the module constant, so the
  next change to `WATERMARK` leaves the two queries with different late-data cutoffs.
- Planting: `start_alerts` inlines `.withWatermark("event_time", "10 minutes")` three lines below
  the `WATERMARK = "10 minutes"` definition.
- Hidden test: Assert the alert query's plan string contains the same watermark delay as `WATERMARK`
  after monkeypatching `WATERMARK` to `"7 minutes"`.

### SS-18: New query has no `queryName`
- Severity: Nit
- Description: The alerts query is started without `.queryName("order_alerts")`, so its metrics and
  `spark.streams.get` lookups show only the random query id and operators cannot tell it from the
  upsert query in the UI or logs.
- Planting: `start_alerts` copies the writer chain from `start`, which also lacks a name, so the
  omission looks consistent. The PR adds log lines that print `q.id`.
- Hidden test: Assert `start_alerts(...).name == "order_alerts"` before calling `awaitTermination`.

## Looks wrong but is fine

### SS-CLEAN-01: Staging write followed by a second overwrite of the target
- Pattern: `upsert_batch` writes `merged` to `f"{target}__staging"`, reads it back, overwrites
  `target`, then removes the staging directory.
- Why it is fine: `merged` is built from `spark.read.parquet(target)`, and Spark cannot overwrite a
  path that is an input of the plan being written; it deletes the files first and then fails
  reading them. The staging hop materializes the result before the target is touched, which is the
  only safe way to rewrite a path in place without a table format.
- What a reviewer might wrongly say: "This writes every batch twice; drop the staging step and
  overwrite the target directly."

### SS-CLEAN-02: Batch `spark.read.parquet(target)` inside foreachBatch
- Pattern: The function passed to `foreachBatch` performs a batch read of the sink table and
  unions it with the micro-batch.
- Why it is fine: The DataFrame handed to `foreachBatch` is a batch DataFrame, so ordinary batch
  reads and writes are allowed there. Reading the current sink each batch is how the merge
  computes "newer of existing and incoming", and the README documents the write as a full merge.
- What a reviewer might wrongly say: "You cannot mix batch and streaming reads; this should be a
  stream-static join declared outside the sink function."

### SS-CLEAN-03: `dropDuplicatesWithinWatermark(["event_id"])` without `event_time` in the subset
- Pattern: The dedupe subset is the id column only, while the watermark is defined on
  `event_time`.
- Why it is fine: `dropDuplicatesWithinWatermark` keys state on the subset and expires each key
  once the watermark passes that row's event time plus the delay, by design. The rule "include the
  watermark column in the subset or state never expires" applies to plain `dropDuplicates`, which
  is exactly why the base job does not use it (see SS-09).
- What a reviewer might wrongly say: "State will grow forever; add `event_time` to the dedupe
  columns."

### SS-CLEAN-04: `unionByName(existing, incoming, allowMissingColumns=True)`
- Pattern: The merge unions the existing table with the incoming rows while allowing missing
  columns.
- Why it is fine: Both sides come from `ORDER_EVENTS_SCHEMA`; the only column that can be absent
  is `_batch_id`, which a target written before that column existed lacks. `latest_per_order`
  then picks one row per `order_id` by `event_time`, so a null `_batch_id` on an older row never
  affects which row wins. Strict `unionByName` would make the first deploy after adding a column
  fail on every batch.
- What a reviewer might wrongly say: "This hides schema drift; a malformed event with missing
  fields will be written as nulls."
