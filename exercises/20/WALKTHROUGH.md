# Exercise 20 walkthrough: customer daily enrichment

Mode: teach. Domain: rewrite:spark_batch (`app/jobs`). Difficulty: rewrite.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/47
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/51
- Feature: `app/jobs/daily_enrichment.py` reads the order partitions for a date
  range and writes `customer_daily_enrichment`: order count, paid total,
  average order value, large order count, and the first and last order hour per
  customer per day.

Read the exercise PR with its inline comments first, then the rewrite PR with
its per-hunk commentary, then this file.

This is a rewrite exercise. There are no planted defects. Every metric is
right, the partition overwrite is safe, the partition filter is on the read,
`dt` stays a string, ruff and mypy are green, and the suite passes. The
exercise is to say what is wrong with the shape, in what order you would change
it, and, just as important, what you would refuse to build.

## How to read this diff

One new job file of 125 lines, a schema, a test, and three lines of README. The
reading order is not top to bottom.

1. **`app/jobs/daily_orders.py`, which is not in the diff.** Open it first.
   This is the move that decides the whole review. The sibling job in the same
   package is 90 lines and has the shape the new one should have had:
   `LakePaths` with a property per table, `read_orders(spark, paths, days)`
   with the schema, the `basePath` option and the `dt` filter, a pure
   `aggregate_daily(orders)`, a four-line `write_daily`, and a `run` that
   composes them. Write that list down. Everything in the review comes from
   comparing the new file against it.
2. **The new file's signature, lines 31 to 38.** Read it before the body.
   `enrich(spark, root: str, start: str, end: str, backfill=False,
   dry_run=False)`. Six parameters, two of them booleans, one of them a raw
   string standing in for a dataclass that already exists. The signature is the
   confession: this function decides which days, where the files are, what to
   compute, and whether to write.
3. **The last line of the body before the write, line 87.** Read the return
   value before the middle. `grouped.drop("gross_total", "avg_raw").select(...)`
   A function that has to drop columns before selecting is telling you it made
   columns it did not want. That is the tell for the chain above it, and you
   have not read the chain yet.
4. **Now the body, one pass, asking only "what job is this line doing".** Mark
   where each job starts: range resolution at 40, path construction at 46, read
   at 47, row-level columns at 54, aggregation at 76, cleanup at 87, write at
   98. Seven jobs, one scope.
5. **`tests/test_daily_enrichment.py`.** Two tests, and read what the first one
   had to do to get at the numbers: `enrich(..., dry_run=True)`. A test using a
   production flag to avoid a side effect is a missing function boundary
   wearing a disguise. That observation is worth more in a review than any
   sentence about readability, because it names a cost.

## What to grep for

- `grep -c "withColumn" app/jobs/daily_enrichment.py`: fourteen. Then
  `grep -c "withColumn" app/jobs/daily_orders.py`: zero. Same package, same
  kind of aggregate, and one file needs fourteen where the other needs none.
- `grep -n "cast(" app/jobs/daily_enrichment.py`: nine casts. Then find how
  many distinct facts they produce. `paid_amt` is cast on line 62 and again on
  line 74; `dt_str` on 64 casts a column `ORDERS_SCHEMA` declares as
  `StringType`; `order_hour_int` on 66 casts what `F.hour` already returns as
  an int.
- `grep -n "root" app/jobs/daily_enrichment.py`: the parameter, then two
  f-strings building paths. Then `grep -n "class LakePaths" -A12
  app/jobs/daily_orders.py` to see that one of those two paths is already a
  property there.
- `grep -n "def " app/jobs/daily_enrichment.py`: two functions in 125 lines,
  and one of them is `main`.
- Count the lines of `enrich`: 73. The repo's next-longest job function is
  `run` in `daily_orders.py`, at six.

## The reasoning chain for each smell

### 1. God function (`enrich`, line 31)

The chain starts at the test, not the code. `tests/test_daily_enrichment.py`
passes `dry_run=True` to get a DataFrame without writing files. Ask why. The
answer is that there is no way to reach the aggregation without also doing the
read and the write, so the test uses a flag to suppress the half it does not
want. That is a testability cost with a name and a line number, which is what
makes it reviewable instead of a matter of taste.

Then check whether the seams are obvious, because a split with unclear
boundaries is worse than no split. Here they are handed to you: read, compute,
write, and `daily_orders.py` in the same package already cuts in exactly those
places with exactly those names. So the rewrite is not a design exercise. It is
matching the file next door.

The subtle part is the read. The new job re-derives the schema, the `basePath`
option and the `dt` filter that `read_orders` already applies. Reimplementing
that is worse than merely duplicated code: convention 4 (partition filter on
every Spark read) now has two implementations, and only one of them is the one
people check. Importing `read_orders` across two job modules is a small cost
and the right trade.

### 2. Twelve-step withColumn chain (lines 54 to 85)

Do not start by counting the calls. Start by asking what facts the aggregate
needs: whether an order counts as paid, whether it is large, what hour it
happened, and how many there were. Four facts. Then count the columns the chain
creates: eight, plus two more after the `groupBy`, and every one of them is
dead by line 87.

Now the individual findings fall out, and each is worth stating separately
because they are different mistakes:

- `is_paid` then `paid_amt` is one expression written as two, with a comparison
  against a literal `1` in the middle that a column expression does not need.
- `paid_amt` is cast twice, to two different precisions, in two places thirteen
  lines apart. Whichever one is load bearing, the other is noise, and a reader
  has to check both.
- `dt_str` casts a column the schema declares as a string. The cast is
  harmless, which is what makes it expensive: it survives forever because
  nobody can prove it is unnecessary without opening `schemas.py`.
- `one` and `one_int` materialize a literal per row so that `sum` can count.
  `F.count` exists.
- The rename on line 86 and the drop on line 87 exist only to clean up after
  the chain. Remove the chain and both vanish, which is the tell that they were
  never doing work.

The fix is to build the three expressions as local `Column` values and pass
them into `agg`, casting once at the aggregate. Point at `aggregate_daily` in
`daily_orders.py` while saying it; the shape already exists in the repo.

### 3. Boolean parameters (lines 36 and 37)

Test each flag by writing a call that a reasonable person would write and
asking what happens. `enrich(spark, paths, "2026-08-01", "2026-08-01",
backfill=True)` reads seven days that have nothing to do with the two arguments
just before it. Nothing in the signature says so. That is the argument against
`backfill`, and it is specific, which is what makes it land.

`dry_run` is a different failure. It makes the return value mean two things:
sometimes "here is what I wrote", sometimes "here is what I would have
written". The caller cannot tell from the value.

The judgment call, and the one an interviewer will push on: the command line
flags stay. Deleting `--dry-run` would break the runbook. What moves is where
the decision is made, which is `main`, next to the argument that causes it.
"Delete the flag" and "move the flag out of the function signature" are not the
same claim, and only the second one is right here.

### 4. Primitive obsession (`root: str`, lines 33, 46 and 101)

The smallest smell and the first commit, because it is three lines and it makes
the next commit possible: once the job takes a `LakePaths`, it can call
`read_orders`, which takes one.

Say the cost concretely rather than in the abstract. The join between a root
and a table name is written in two files now. Adding the next lake table means
editing whichever file the author happens to be in, and the two will drift in
formatting first and content later. One dataclass with one property per table
is the fix, and it already exists.

## The clean-code trap

Line 79, `F.sum("total_amt").alias("gross_total")`, feeding the division on
line 84. `paid_total` on line 78 filters to paid, shipped and delivered.
`avg_order_value` does not filter at all. Two aggregates over the same rows
with different denominators, twelve lines apart, look exactly like a
copy-paste that lost its `when`.

It is the specified behavior. The PR description says the dashboard wants the
average across every order placed that day, and the test pins it: customer 1
has a 10.50 paid order and a 40.50 cancelled one, and the expected average is
25.50, not 10.50. Asserting a bug here is a false positive. Asking the author to
confirm the spec, or asking for a comment naming the difference, costs nothing
and is the better review comment.

Note how the rewrite treats it: the expression is rewritten into the `agg`
alongside everything else, and the denominators stay different. Preserving
something you were suspicious of, on purpose, and saying so in the PR
description, is the part of a rewrite that is hardest to fake.

## Design and tests

A strong reviewer does not treat the four smells and the test gap as separate
passes. They fall out of the same read.

- **RW-15, the god function.** Noticed at the test, not the code: the shipped
  test has to pass `dry_run=True` to get a DataFrame back without writing
  files. A flag standing in for a missing function boundary is visible before
  you have read a single line of `enrich`'s body.
- **RW-04, the withColumn chain.** Noticed by counting: four facts go in
  (paid, large, hour, count), and eight-plus columns come out before the
  `groupBy`, every one dead by the `drop` on line 87. A chain whose output the
  function throws away is a chain doing work nobody asked for.
- **RW-06, the boolean flags.** Noticed by writing the call a caller would
  actually write: `enrich(spark, paths, "2026-08-01", "2026-08-01",
  backfill=True)` silently ignores the two dates right next to it. The
  signature does not warn you, which is the finding.
- **RW-08, the raw root string.** Noticed by grep: `daily_orders.py` already
  has a `LakePaths` property for the sibling table, so a second `f"{root}/..."`
  a few lines away in a sibling file is a path being invented instead of
  reused.
- **TR-03, the boundary skipped.** Noticed by locating the one number the
  feature introduces, `LARGE_ORDER_TOTAL = Decimal("50.00")`, and then
  checking whether any test uses that exact value. `test_large_order_flag`
  uses 49.00 and 51.00, a dollar either side, so a reviewer can state the
  consequence precisely: flip `>=` to `>` in the job and the whole suite
  still passes. That is the difference between "add more tests" and a comment
  that names what a mutation would get away with.

Two interviewer questions about these findings:

1. `test_large_order_flag` is a new test, not an old one that grew stale. Why
   would a careful author write a threshold test and still miss the threshold
   value, and what habit would catch this at review time rather than at
   incident time?
2. Of the four smells, which one would you insist on before merge and which
   would you let ship and follow up on, and what changes your answer, the
   size of the team or the size of the file?

## Questions to ask the author

- `tests/test_daily_enrichment.py` uses `dry_run=True` to get the numbers
  without writing. If that flag did not exist, how would you test the
  aggregation?
- `paid_amt` is cast to `decimal(12,2)` on line 62 and `decimal(14,2)` on line
  74. Which one is load bearing, and what breaks if I delete the other?
- `backfill=True` makes `start` and `end` do nothing. Is there a caller that
  passes both, and how would they find out?
- `avg_order_value` averages every order while `paid_total` counts only paid.
  Is that the dashboard spec or a slip? What would you expect the number to be
  for a customer whose only order that day was cancelled?
- `read_orders` in `daily_orders.py` does the same read with the same schema
  and the same partition filter. What stopped you from calling it?

## Five questions an interviewer would ask about the rewrite

1. You import `read_orders` from `daily_orders` into `daily_enrichment`, so one
   job module now depends on another. What did you consider instead, and at
   what point would you extract the read into a third module rather than keep
   the import?
2. The average keeps a different denominator from `paid_total` after your
   rewrite. Walk me through how you convinced yourself that was intended rather
   than a bug you should have fixed while you were in there.
3. Your second commit is a pure move with the twelve `withColumn` calls
   unchanged inside the new function, and the third collapses them. The
   reviewer only ever sees the final diff. Why was the split worth it?
4. You deleted `dry_run` from the signature but kept `--dry-run` on the command
   line. What is the rule that decides which flags are allowed to exist, and
   where?
5. You kept the cast inside `agg` rather than casting `total` once at read time
   in `read_orders`. Both are one line. What made you choose the one that keeps
   the change inside this job?
