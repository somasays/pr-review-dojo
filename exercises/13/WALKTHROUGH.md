# Exercise 13 walkthrough: per-customer paid order counts

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/34
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/40
- Mode: teach, domain spark_streaming, difficulty easy

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## Reading order for this diff

A streaming diff is not read top to bottom. The question that orders it is
"what runs, how often, and what happens when it runs twice". So:

1. **`main` first.** It tells you how many queries now exist, what each one
   writes, and which checkpoint each one uses. Two queries on one source with
   two checkpoints and two targets is the shape you are about to review. If
   the same checkpoint had been handed to both, nothing else in the diff
   would matter, so check that before reading any Spark code.
2. **The new reader, side by side with the old one.** Put `read_events` and
   `paid_events` next to each other. They read the same directory, so any
   difference between them is either a deliberate decision or an accident.
   Go line by line: schema, options, watermark, dedupe, filter.
3. **The new `foreachBatch` function, side by side with `upsert_batch`.**
   Same trick. `upsert_batch` is the house pattern for a sink write in this
   repo, and its docstring says "safe to replay". Ask whether the new one is.
4. **The writer chains.** Trigger, checkpoint option, query name.
5. **The tests last,** to see which of the above they actually exercise. Here
   they cover one batch and one run, which is exactly the region where all
   the defects are invisible.

Things worth grepping before commenting:

- `grep -n "dropDuplicates" app/jobs/order_events_stream.py` shows two
  different calls in one file.
- `grep -n "maxFilesPerTrigger\|trigger(\|queryName\|checkpointLocation"`
  gives you the whole streaming surface in ten lines.
- `grep -rn "WATERMARK\|10 minutes"` finds a constant and a literal that
  should be the same thing.
- `git log -p --follow app/jobs/order_events_stream.py` for the base job's
  intent. The README section on the lake and the "all writes are idempotent"
  convention are the standard this PR is measured against.

Questions to ask the author, in the order they occur while reading:

- What happens to this table if Spark reruns batch 7?
- Why does the new reader dedupe differently from the existing one?
- What does the state store look like a month after this ships?
- Was the one second trigger measured, or copied from a local run?

## Reasoning chain, defect by defect

### The counts merge is not replay safe (Blocker)

`upsert_batch` is idempotent because it recomputes: given the same inputs it
produces the same table, and replaying it changes nothing. `merge_paid_counts`
looks like the same shape, staging hop and all, but the operation in the
middle is `sum`, not "newest per key". Addition has no fixed point.

`foreachBatch` is at-least-once. Spark commits the offsets after the function
returns, so a failure in between means the same `batch_id` runs again. The
table has no record of which batch produced it, so the second run adds the
same customers again. Nothing downstream can tell an inflated count from a
real one, and no later batch corrects it. That is data corruption, silent and
permanent, which is what makes it the blocker rather than a Major.

The tell in the diff: the batch function takes `batch_id` and never uses it
except in the log line. In a streaming sink, an unused `batch_id` is a
question waiting to be asked.

### `dropDuplicates` instead of `dropDuplicatesWithinWatermark` (Major)

These two are not spelling variants. Plain `dropDuplicates` on a streaming
frame keeps every key it has ever seen unless the watermark column is part of
the key, so with `event_id` alone the state store grows by one row per event
forever. `dropDuplicatesWithinWatermark` expires each key at that row's event
time plus the delay, which is why the base job uses it.

The reasoning that gets you there without knowing the API by heart: the new
reader sets a watermark, so state is meant to expire; the key is an id with no
time in it; ask what tells Spark when this key is safe to forget. Nothing
does. Then check the sibling function, which uses the other call, and ask why
they differ.

This one takes weeks to show up. The job is fine in staging, fine in the first
week of production, then the executors die on state size and the checkpoint
has to be cleared by hand.

### `maxFilesPerTrigger` missing (Major)

The existing reader bounds each micro-batch to ten files; the new one does
not. On a healthy day this is invisible, because the source has one small
file per trigger. It matters on the first trigger after an outage, when the
backlog is thousands of files: the new query pulls all of it into one
micro-batch, and its batch function then does a distinct, a group by, and a
full rewrite of the counts table over the whole thing.

Notice the pattern the two Majors share: both are correct on the fixture and
wrong on the second Tuesday of an incident. That is the standing question for
a streaming review, "what does this do at the moment the system is already
unhealthy".

### One second trigger (Minor)

Cheap locally, expensive on an object store, where every trigger is a
directory listing. It is a Minor rather than a Major because the failure mode
is cost and noise, not wrong data, and because the fix is one line with no
migration. The signal in the diff is the inconsistency: 30 seconds two
functions above, one second here, with a comment explaining the intent, which
means it was deliberate and can be discussed rather than simply corrected.

### Inlined watermark literal, missing query name (Nits)

Both are the same kind of remark: they cost nothing now and cost a little
later. Keep them last in the review and say plainly that they do not block.
A review that opens with the missing `queryName` has told the author the
counts can be wrong forever is roughly as important as a log label.

### The clean trap

The staging write in `merge_paid_counts` looks like an obvious waste: write
the result, read it back, write it again. It is not. `merged` is built from
`spark.read.parquet(target)`, and Spark truncates the output path before it
evaluates the plan, so overwriting `target` directly fails while reading its
own input. The staging hop materializes the result first. The same pattern
sits in `upsert_batch`, unchanged, with a comment explaining it.

"This writes twice, drop the staging step" is the false positive this exercise
plants. The batch `spark.read.parquet(target)` inside `foreachBatch` is the
other thing that looks wrong and is fine: the frame handed to `foreachBatch`
is a batch frame, and reading the sink each batch is how the merge computes
"existing plus incoming".

## Five questions an interviewer would ask about the rewrite

1. The fix stores the applied `batch_id` on the counts table and skips a
   batch that is already applied. What does it cost you if the write of the
   target fails halfway through, and how would you make the sink atomic?
2. Why keep one row per customer instead of writing per batch delta
   partitions and summing on read? Name a workload where you would pick the
   other answer.
3. The guard compares against the maximum stored `_batch_id`. What breaks if
   two queries ever write this table, or if the checkpoint is cleared and
   batch ids restart at zero?
4. `dropDuplicatesWithinWatermark` dedupes only within the watermark. What
   does a producer retry that arrives eleven minutes late do to this count,
   and how would you decide whether that matters?
5. The rewrite deliberately did not name the existing upsert query and did
   not extract a shared reader helper. Argue both decisions, then argue the
   opposite.
