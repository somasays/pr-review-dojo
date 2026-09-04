# Exercise 04 walkthrough: customer search by name prefix and region

Teach mode, sqlalchemy, easy. Read the exercise PR and its inline comments
first, then the rewrite PR, then this file.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/8
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/12

## Reading order

A search endpoint is three layers, and only one of them is interesting.
Read them in the order that lets you throw work away early.

1. **The PR description, once.** Two things to keep: "no boxes means every
   region" and "the response carries `total` next to the page". Both are
   promises about behavior. Most of the defects below are one of those two
   promises not being kept.
2. **`app/db/repositories.py`.** This repository's convention 1 says every
   query lives here, so this is where a search PR either goes wrong or does
   not. Read the three new methods before you read anything else.
3. **`app/db/models.py` and `app/db/alembic/versions/`.** Not because the
   diff touches them. Because a new query means a new access path, and you
   are checking whether anything supports it. In this PR neither file is in
   the diff at all, and that absence is the point.
4. **`app/api/routers/customers.py`.** Now that you know what the queries
   do, check that the endpoint feeds them what it claims to.
5. **`app/api/schemas.py`.** Quick pass: is the response model still an
   allowlist, does it leak a column the endpoint should not return.
6. **`tests/test_customer_search.py`.** Last, and read it as evidence, not
   as reassurance. Ask what it does not cover.

## What to grep for

Before reading closely, run these. Each one takes a second and each one maps
to a house convention that a database PR tends to break.

| Grep | Convention it protects | What it finds here |
| --- | --- | --- |
| `git diff main... -- app/db \| grep -nE 'f"|%s|\.format\('` | 1, no interpolated SQL | The `region IN (...)` list in `search_count` |
| `git diff main... \| grep -n 'order_by'` | deterministic paging | `order_by(Customer.name)` with `limit`/`offset` |
| `git diff main... \| grep -nE 'in_\(\|\.any_\('` | empty collection guards | `Customer.region.in_(regions)` with no guard |
| `git diff main... --stat \| grep -E 'models.py\|alembic'` | new access path needs an index | Neither file appears |
| `git diff main... \| grep -nE 'session\.query\(\|\.commit\(\)'` | 8, repositories never commit, and the 2.x style | `self.session.query(Customer)` |
| `git diff main... -- tests \| grep -c 'def test'` | 6, every public function has a test | Seven tests, none of which touch the empty-region path |

The greps do not find the defects. They tell you which five lines to think
hard about, which is the part that actually costs time under a clock.

## The reasoning chain, defect by defect

### 1. Interpolated region values in `search_count` (Blocker)

The chain starts at the f-string, but the f-string alone is not the finding.
Plenty of f-strings in SQL are harmless because the value is a constant.
The question is always: **can a request reach this string?**

Follow it backwards. `search_count(q, regions)` is called by
`search_customers`. `regions` comes from `_clean_regions(region)`.
`_clean_regions` trims, upper-cases and de-duplicates. It does not validate.
`region` is `Annotated[list[str] | None, Query(alias="region")]`, so it is
whatever the client sent. Three hops and the query string is inside the SQL
text. That is the finding.

Now make it concrete, because "could be exploited" loses arguments and a
payload ends them. `region=US-CA') OR name LIKE ('%` produces:

```sql
SELECT COUNT(*) FROM customers WHERE name LIKE :pattern
AND region IN ('US-CA') OR name LIKE ('%')
```

The `OR` takes over and the count becomes every customer. The same shape
reaches any column, and on a database that allows stacked statements it
reaches more than `SELECT`.

Two lines up, `:pattern` is bound correctly. That contrast is the tell that
this was a slip rather than a house style, and it is also what makes "raw
SQL is banned here" the wrong comment to leave.

### 2. No index for the new access path (Major)

This one is found by absence, which is the hardest kind to see because
nothing on screen is wrong. The habit that finds it: **every new WHERE
clause is a question about an index.**

The predicate is `region = ?` plus `name LIKE 'x%'` plus `ORDER BY name`.
Check `models.py`: `Customer` has a unique index on `email` and nothing
else. Check the migrations directory: 0001 and 0002, and 0002 exists
precisely because someone added `ix_orders_customer_created` for the order
listing. So the codebase already agrees that this matters; this PR just did
not do it.

The second half of the finding is that the model alone would not be enough.
Tests build the schema with `Base.metadata.create_all`, so a model-only
index passes CI and is missing in production. The fix needs both.

### 3. Ordering with no tiebreaker (Major)

The trigger is the pair `order_by(...)` and `offset(...)` in the same
statement. Whenever you see both, ask whether the sort key is unique.

`name` is a `String(120)` with no unique constraint, and the whole point of
the feature is that several customers can share the start of a name. So the
sort is partial: rows that tie are returned in whatever order the storage
engine finds convenient, and that order is not guaranteed to be the same for
the `offset=0` query and the `offset=2` query. The operator sees a duplicate
on page two and never sees the row it displaced.

The reason the PR's own paging test passes is worth saying out loud in the
review: SQLite breaks ties by rowid, consistently, so the test proves
nothing about Postgres. `list_for_customer` in the same file already orders
by `created_at, id`, so the fix is to be consistent rather than clever.

### 4. `in_([])` on the documented default (Minor)

Read the docstring on the endpoint: "With no `region` checkbox ticked the
search covers every region." Read `_clean_regions`: it returns `[]` for that
case. Read `search`: it passes `[]` straight into `in_()`.

`IN ()` has no rows, so SQLAlchemy compiles an always-false predicate. The
page is empty. Meanwhile `search_count` has an `if regions:` guard, so
`total` is correct. The response says "84 matches" above an empty list.

Two details make this a good find rather than a lucky one. First, the two
methods disagree with each other, and a disagreement inside one diff is
almost always a defect in one of them. Second, `ProductRepository.by_skus`
in the same file opens with `if not skus: return {}`, so the house already
knows about this trap.

Severity is Minor rather than Major because nothing is lost or corrupted and
the operator sees an obviously wrong screen rather than a subtly wrong one.
It is still the first thing anyone will report.

### 5 and 6. The two nits in `first_match`

The docstring says "or None" and the body raises `NotFound`. That is a
one-line fix and a real cost: the next caller writes `if row is None` and
that branch never runs, so the 404 handler the app installs is bypassed by
dead code that looks defensive.

The `session.query(...).filter(...)` call is the 1.x API in a file that is
otherwise `select()` and `session.scalar`. Nothing breaks. It matters only
because it is the version someone copies next.

Neither is worth more than a sentence in a review, and neither should be
listed before the injection. Prioritization is graded.

### The clean code

`search_count` builds its SQL with `text()` and a named `:pattern`
parameter. README convention 1 bans f-strings, `%` formatting and
`.format` in SQL and explicitly allows `text()` with named bound
parameters. The LIKE value travels to the driver as a parameter, so nothing
about that line is an injection.

The trap works because the same method contains a genuine injection eight
lines down. Once you have found one, the temptation is to condemn the
technique instead of the mistake, and "rewrite this as `select()`, raw SQL
is banned" costs five points as a false positive. Asking whether the count
deserves its own raw statement is free.

## Questions to ask the author

1. What does the console send when no region is checked, and what does the
   endpoint return for that request today?
2. Which index do you expect this query to use?
3. Two customers are both called Nina. What does page two look like?
4. Where do the `region` values come from before they reach `search_count`?
5. `total` and `items` come from two different queries. What guarantees they
   agree?

Every one of these is a question rather than an accusation, and each one
walks the author into a defect without naming it.

## Five questions an interviewer would ask about the rewrite

1. The count query stays raw SQL after the fix. Defend that, given that the
   codebase has a perfectly good `select(func.count(...))`.
2. Why `(region, name)` and not `(name, region)`, and what changes if the
   endpoint later supports "contains" instead of "starts with"?
3. The index is added to the model and to a migration. What breaks if you
   add it only to the model? Only to the migration?
4. You added `Customer.id` as a tiebreaker instead of moving to keyset
   pagination. At what point does that answer stop being good enough, and
   what would you measure to know you had reached it?
5. The empty-region guard now lives in the repository. Argue for putting it
   in the router instead, then say why you did not.
