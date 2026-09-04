# Exercise 06 walkthrough: weekly customer summary

Mode: teach. Domain: spark_batch. Difficulty: easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/24
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/27
- Feature: `app/jobs/weekly_summary.py` rolls `daily_customer_orders` up to one
  row per customer per week, joins a new `customers` dimension for the region,
  and writes `weekly_customer_summary` partitioned by `week_start`.

Read the exercise PR with its inline comments first, then the rewrite PR with
its per-hunk commentary, then this file.

## How to read this diff

Four files. GitHub will show you `app/jobs/fixtures.py` first because it sorts
alphabetically. Read them in this order instead.

1. **`app/jobs/schemas.py`.** Two new table schemas. In a lake codebase the
   schema constants are the contract: they say what the table is called, what
   the columns are named, and what type money has. Read them first and carry
   the column names in your head. You will need them twice, once for the
   aggregate and once for the writer.
2. **`app/jobs/weekly_summary.py`, top to bottom, but in three passes.**
   - Pass one, the reader and the writer only. In a batch job those two
     functions decide what the job costs and whether a rerun is safe.
     Everything between them decides what the numbers are, and wrong numbers
     are cheaper to fix than a job that cannot finish.
   - Pass two, the aggregate and the join.
   - Pass three, `run` and `main`, which is where actions hide.
3. **`app/jobs/fixtures.py`.** Small, and it tells you what the author tested
   against. In this PR the dimension fixture has exactly one row per customer
   and covers every customer in the orders fixture, which is what makes the
   join look safe.
4. **`tests/test_weekly_summary.py`.** Last, and read it for what it does not
   do. It builds the dimension from the fixture writer, so it can never see a
   customer that is missing from the dimension. It runs on three days, so it
   can never see a full table scan. It asserts the column names the code
   produces, so it can never see them drift from the schema.

## What to grep for

Batch review is mostly five greps.

- `read` and `.parquet(` in the new module. For every read, ask one question:
  what prunes it? Here `read_daily` has a schema, a `basePath`, and no
  predicate at all. Compare it against `read_orders` in `daily_orders.py`,
  which is eight lines away in the same package and has the filter.
- `.filter(` in the new module. Then, for each one, ask what is below it in
  the plan. A filter above a `groupBy` on a column that does not exist in the
  source cannot become a partition filter, no matter how selective it looks.
- `.join(` in the new module. Read the third argument. If there is no third
  argument, it is an inner join, and the question is what rows the right side
  does not have.
- `repartition` and `partitionBy` in the same statement. If the two columns
  differ, ask why. `write_daily` in `daily_orders.py` is the reference: it
  repartitions on the same column it partitions by.
- `count()`, `collect()`, `toPandas()`, `first()` anywhere outside a test.
  Every one is an action, and an action inside a job that also writes means
  the plan runs more than once.

Two greps that pay for themselves on any Spark diff and find nothing here, so
run them and move on: `udf` and `cast("double")`.

## The reasoning chain for each finding

**The daily read is never pruned (Blocker).** The tell is a filter that looks
like it does the job. `roll_up_weeks` ends with
`.filter(F.col("week_start").isin(week_keys(days)))`, and there is a comment
above it saying the filter is applied on the smaller side. Both halves of that
sentence are true and the conclusion is wrong. The aggregate is smaller, and
filtering it is cheap, but the cost of this job is not the filter, it is the
scan underneath. `week_start` is computed by `week_start_column()` from `dt`
inside the same plan, so Spark has a filter on a derived column above a
`groupBy` and nothing it can hand to the file source. Go back to `read_daily`
and there is no `dt` predicate at all. On the three day fixture the job reads
three files. A year into this table it reads three hundred and sixty five
directories to produce seven days of numbers, and it gets slower every day it
runs while the output stays the same size, which is the shape of the incident:
nothing breaks, the job just stops finishing before the dashboard is due.

The general lesson is worth more than the finding: in Spark, a filter is only
a partition filter if it is expressed on the partition column, directly, below
every shuffle. Check the physical plan rather than the source line. This one
shows `PartitionFilters: []` on the daily scan.

Right next to that read is the thing on this diff that looks wrong and is
fine: `.option("basePath", paths.daily_customer_orders)` on a read that then
passes the same path to `.parquet(...)`. It is easy to call that redundant,
delete it, and feel productive. `basePath` is what tells Spark where the
partition columns begin, so `dt` comes back as a column instead of vanishing,
and `week_start_column()` reads `dt`. Deleting it breaks the job. A reviewer
who spends their attention on line 45 and skips line 46 has read the wrong
half of the same expression, which is exactly the trade this exercise is built
to punish.

**The dimension join drops customers (Major).** `weekly.join(customers,
"customer_id")` has two arguments, so it is an inner join. The question that
turns that from a style observation into a finding is: which customers are not
in the dimension? The fixture answer is none, which is why the test passes.
The production answer is the customer created an hour ago, the account that
was deleted, and whatever id guest checkout uses. Then ask what the failure
looks like. It is not an error and it is not an empty report. It is a weekly
revenue number that is quietly lower than the daily table it was built from,
in a report whose entire purpose is to be trusted without checking. Silent
undercounting in a finance report outranks almost anything that throws.

The second half of this finding is a fix that is only half a fix. `how="left"`
keeps the row and leaves `region` null, against a field that
`WEEKLY_CUSTOMER_SCHEMA` declares non-nullable. And once `region` can be null,
the next request, "exclude the internal accounts", puts
`F.col("region") != "INTERNAL"` after the join and turns it straight back into
an inner join, because null compares to nothing. Coalescing to an explicit
`unknown` at the join closes both.

**The writer scatters files (Major).** `df.repartition("customer_id")` in
front of `.partitionBy("week_start")`. Repartitioning by the join key is a
real technique and it is why this reads as an optimization. The question is
what the writer does with those partitions. Each task holds rows for many
customers and one week, so every task writes a file into the week directory:
one file per customer hash bucket per week. On the fixture with four shuffle
partitions that is a handful of files. In production it is a couple of hundred
small parquet files per week, and every downstream read pays the listing cost
forever. Note that this defect is invisible in local tests unless you turn AQE
off, because adaptive coalescing hides it at fixture scale, which is a good
argument for reading the writer rather than trusting the output.

**The log line runs the job twice (Minor).** `log.info("writing %d customer
weeks", weekly.count())`. Observability is a good instinct and this is the
expensive way to have it. `count()` is an action, so the read, the aggregate
and the join all execute, and then `write_weekly` executes them again. The
line above already logs the week keys, which is the part an operator uses. If
a row count is wanted, it is in the write metrics.

**Two Nits worth a sentence each.** The new lake sub-paths are built inline
with `f"{paths.root}/..."` in two places while the `LakePaths` dataclass with
`orders` and `daily_customer_orders` properties sits at the top of the module
they import from. And the aggregate aliases `n_orders` and `total` while
`WEEKLY_CUSTOMER_SCHEMA` declares `order_count` and `paid_total`. The second
one has a tell: the new test asserts the aliases. When a test agrees with the
code and disagrees with the schema constant, the test is not evidence, it is a
copy of the code under review.

## Priority order, and why

Blocker first: the job that scans the whole table gets worse every day and
nobody notices until it misses the window. Then the inner join, because wrong
numbers in a finance report are worse than slow numbers, but this one at least
fails the same way every run. Then the file layout, which is a debt that lands
on other teams rather than this one. The `count()`, the paths and the names
are all real and none of them should hold up a merge on their own. Say so
explicitly in the summary: a review that lists six problems without ranking
them makes the author guess, and they will guess wrong.

## Five questions an interviewer would ask about the rewrite

1. The fix widens the requested range to whole weeks with `covered_days`
   before building the partition keys. What breaks if you prune to exactly the
   days requested instead, and how would you notice?
2. `read_daily` filters with `isin` over a list of `YYYY-MM-DD` strings. A
   colleague proposes parsing `dt` to a date and using `between`, which reads
   better and handles long ranges without a long `IN` list. What do you tell
   them, and what would you show them to settle it?
3. The join fix coalesces a missing region to `"unknown"` rather than leaving
   it null or failing the run. Argue the case for failing the run instead.
   Under what operational setup does that become the right answer?
4. `write_weekly` repartitions by `week_start` before a `partitionBy(
   "week_start")` write. The job now backfills a year in one run, so that is
   fifty two output partitions from one shuffle. At what point does one file
   per week stop being the right target, and what would you change first?
5. None of these fixes needed a new abstraction, and the rewrite adds one
   four-line function. Where in this module would you expect the first real
   abstraction to be justified, and what would have to happen to justify it?
