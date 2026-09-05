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
   writes, and which checkpoint each one uses. Three queries on one source
   with three checkpoints and three targets is the shape you are about to
   review. If any two shared a checkpoint, nothing else in the diff would
   matter, so check that before reading any Spark code.
2. **The new reader, side by side with the old one.** Put `read_events` and
   `paid_events` next to each other. They read the same directory, so any
   difference between them is either a deliberate decision or an accident.
   Here they match line for line except for the status filter, which is the
   right amount of difference for "the same stream, one more predicate".
3. **The two new `foreachBatch` functions, side by side with `upsert_batch`.**
   `upsert_batch` is the house pattern for a sink write in this repo, and its
   docstring says "safe to replay". Ask whether each new one is, and whether
   it needed a streaming aggregate at all or could have merged batch deltas
   the same way `upsert_batch` does.
4. **The writer chains.** Trigger, checkpoint option, query name, output
   mode. `outputMode("complete")` on a `groupBy` with no time-bounded key is
   the detail worth stopping on.
5. **The tests last,** to see which of the above they actually exercise. Here
   the shipped test for the counts query never runs a second batch against an
   existing table, which is exactly the region where the merge defect lives.

Things worth grepping before commenting:

- `grep -n "outputMode\|groupBy" app/jobs/order_events_stream.py` finds the
  one query that aggregates instead of merging, and asks whether its key has
  a time component a watermark could expire.
- `grep -n "maxFilesPerTrigger\|trigger(\|queryName\|checkpointLocation"`
  gives you the whole streaming surface in a dozen lines.
- `grep -n "__staging"` finds every sink that stages before it overwrites,
  which is worth comparing for duplication.
- `git log -p --follow app/jobs/order_events_stream.py` for the base job's
  intent. The README section on the lake and the "all writes are idempotent"
  convention are the standard this PR is measured against.

Questions to ask the author, in the order they occur while reading:

- What happens to the counts table if Spark reruns batch 7?
- Why does the customer totals query need `outputMode("complete")`, and what
  does its state store look like a year after this ships?
- Was the one second trigger on the counts query measured, or copied from a
  local run?
- Did the test for the counts merge ever run against a table that already
  has rows in it?

## Reasoning chain, finding by finding

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

### Customer totals query aggregates in complete mode over an unbounded key (Major)

`customer_running_totals` groups by `customer_id` and the writer uses
`outputMode("complete")`, which Spark requires for a non-windowed aggregate.
That requirement is also the problem: `customer_id` has no time component a
watermark could expire it by, so the state store holds one row per customer
for the life of the checkpoint, and every trigger recomputes and rewrites
every customer ever seen, not just the ones in this batch. Both the state
size and the per-trigger write grow with total customer count, never
shrinking.

The reasoning that gets you there without knowing the API by heart:
`outputMode("complete")` plus `groupBy` is a phrase worth pausing on whenever
the grouping key is an id rather than a window. Ask what makes an old key
leave state. If the answer is "nothing", that is the finding. The fix already
exists two functions above: `merge_paid_counts` merges a batch delta into a
plain table inside `foreachBatch`, with no streaming aggregation and no
unbounded state, and the totals query could do the same thing with `F.sum`
in place of `F.countDistinct`.

### The counts test never runs a second batch (Major)

The only test that exercises `start_paid_counts` end to end always starts
from an empty target. `merge_paid_counts` has a second branch, read the
existing table and add to it, that only runs once a target exists, and that
branch is exactly where the replay defect above lives. A test suite that
never populates the table before merging into it cannot fail on that defect
no matter how the code changes.

The habit this builds: whenever a function branches on "does the target
already exist", ask whether the test suite ever puts it in both states.

### One second trigger on the counts query (Minor)

Cheap locally, expensive on an object store, where every trigger is a
directory listing. It is a Minor rather than a Major because the failure mode
is cost and noise, not wrong data, and because the fix is one line with no
migration. The signal in the diff is the inconsistency: 30 seconds two
functions above, one second here, with a comment explaining the intent, which
means it was deliberate and can be discussed rather than simply corrected.

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

## Design and tests

A design or refactor finding in this file never shows up as a runtime
failure on the fixture; you only see it by asking "will the next feature pay
for this shape". Two ways in:

- **Compare a new function to its closest sibling.** `batch_paid_counts` and
  `upsert_batch`'s staging block both exist next to a near-identical
  neighbor: `latest_per_order` for the merge shape, and `upsert_batch`'s own
  stage-then-overwrite dance for the write. When two functions in the same
  file do the same four lines a different way, or the same four lines the
  same way without sharing them, that is the design smell, not a runtime bug.
  `merge_paid_counts` (line 116) stages, writes, overwrites, and removes the
  staging directory with the exact same four lines `upsert_batch` (line 77)
  already has above it. Nothing here is wrong today; the cost lands the next
  time a third sink function needs the same rewrite and copies it again
  instead of calling a shared helper.
- **Ask whether a built-in already does this.** `batch_paid_counts` computes
  "how many distinct orders per customer" with `.select(...).distinct()
  .groupBy("customer_id").agg(F.count("*"))`. Before accepting a multi-step
  pipeline, ask whether one aggregate function already expresses the same
  intent: `F.countDistinct("order_id")` under the same `groupBy` does, in one
  step instead of a shuffle followed by another shuffle. This is the kind of
  finding you catch by naming what the code computes in one sentence, then
  checking whether the API has a name for exactly that sentence.
- **Refactor, not defect: read the signatures, not just the bodies.** `start`,
  `start_paid_counts`, and now `start_customer_totals` all take the same
  `(source_dir, target, checkpoint)` trio, and `main` parses the same pair of
  CLI flags a third time to build it. No single occurrence is a problem; the
  third repetition is the signal that a small `StreamPaths` grouping would
  pay for itself, and it is exactly the kind of thing you flag as "worth
  doing, not blocking" rather than requesting changes over.
- **For the test finding, ask what state the test starts from.** A test that
  calls a merge function once, against an empty table, can never reach the
  "existing rows plus this batch" branch. Whenever a function's body branches
  on whether something already exists, check whether any test in the file
  ever makes that branch true before this PR's change was applied.

Two interviewer questions about these:

1. `merge_paid_counts` and the fixed `merge_customer_totals` are now nearly
   identical: read existing, guard on `_batch_id`, merge, stage, overwrite.
   Why does the reference fix not also extract a shared `merge_running_value`
   helper parameterized by the aggregate function, and when would you push
   back and say it should?
2. The `StreamPaths` refactor is applied to `start_paid_counts` but not to
   `start` or `start_customer_totals`. Is that the right amount of fix for a
   Minor, not-blocking finding, or does leaving two of three functions on the
   old signature create its own inconsistency?

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
4. The customer totals fix drops the streaming aggregate entirely in favor of
   a batch merge. What would make the aggregate the right call after all, and
   what would you need to add to make it safe at that point?
5. The rewrite deliberately did not extract a shared reader helper for
   `read_events` and `paid_events`, even though they are now identical except
   for one filter. Argue both decisions, then argue the opposite.
