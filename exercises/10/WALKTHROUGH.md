# Exercise 10 walkthrough: order shipment tracking

Mode: teach. Domain: migrations. Difficulty: easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/15
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/22
- Feature: `orders` gains `shipped_at` and `tracking_number`,
  `POST /orders/{order_id}/ship` takes an optional tracking number, and
  `OrderOut` returns both fields.

Read the exercise PR with its inline comments first, then the rewrite PR
with its per-hunk commentary, then this file.

## How to read this diff

Six files, and five of them are almost noise. The whole review lives in one
44 line migration. That is normal for schema work: the application code is
easy to test and easy to roll back, the migration is neither.

1. **`app/db/models.py`.** Two lines. Read them first anyway, because they
   are the specification the migration has to match: `tracking_number` is
   `String(64)`, `NOT NULL`, Python default `""`; `shipped_at` is
   `DateTime(timezone=True)`, nullable. Write those four facts down. You are
   about to compare them against the migration one at a time.
2. **`app/db/alembic/versions/add_shipping_fields.py`.** Read `upgrade` and
   `downgrade` as two separate reviews. For `upgrade`, ask what happens on a
   table with fifty million rows, not on the empty SQLite database the tests
   use. For `downgrade`, ask what the database looks like after a rollback,
   and whether anything is unrecoverable.
3. **`app/api/schemas.py`.** Confirm the new request field is bounded and
   that the response addition is deliberate. Both are fine here.
4. **`app/services/order_service.py`.** Check the write path against the
   model: does it ever write a value the column cannot hold, and does a
   repeated call change what was already recorded. Both fine.
5. **`app/api/routers/orders.py`.** An optional body on an existing POST.
   Confirm the old call with no body still works, since a test in the repo
   already makes that call.
6. **`tests/test_api_order_shipping.py`.** Read it for what it does not
   cover. Every test here runs against `Base.metadata.create_all`, which is
   the model, not the migration. Nothing in this file can fail because of
   anything in the migration. That gap is the whole exercise.

## What to grep for

- `downgrade` in `app/db/alembic/versions/`. Read every `drop_column` and
  every `drop_index` against the `add_column` and `create_index` calls in the
  same file. They must be the same set of objects. Here they are not.
- `server_default` in `app/db/alembic/versions/0001_initial.py`. Every
  `NOT NULL` column on `orders` has one. The new column has one for two
  statements and then loses it.
- `op.execute` across `app/db/alembic/`. Zero hits before this PR. One after.
  In a schema migration that is a question every time.
- `timezone=True` in `app/db/models.py` and in `0001_initial.py`. Four
  timestamp columns, all aware. The fifth one is aware in the model and naive
  in the migration.
- `create_all` in `conftest.py`. This tells you the API tests never run a
  migration, so the migration has exactly one test in the whole repo:
  `tests/test_migrations.py`.
- Read `tests/test_migrations.py` itself, all fifteen lines. It upgrades to
  head and downgrades to `base`. Ask what a downgrade to `0002` would do,
  because that is the rollback that actually happens in production, and
  nothing tests it.

## The reasoning chain for each finding

**The downgrade drops the wrong column (Blocker).** The upgrade adds
`shipped_at` and `tracking_number`. The downgrade drops `shipped_at` and
`currency`. Read those two lists side by side and it is obvious; read the
file top to bottom and it is easy to miss, because `drop_column("currency")`
is perfectly valid code sitting under a perfectly reasonable
`batch_alter_table`. Now finish the thought. `currency` is `NOT NULL` on
every order, there is no other copy of it in the schema, and a rollback is
the thing you do at 2am when the new release is broken and you are not
thinking carefully. This is not a rollback that fails loudly, it is a
rollback that succeeds and silently destroys a column. The reason no test
catches it is worth stating in the review: `tests/test_migrations.py` goes
straight to `base`, and `0001` drops the table anyway, so the wrong
`drop_column` never has a visible effect in CI.

**The server default is added and then removed (Major).** The two statements
are separated by a comment that sounds like discipline: one source of truth
for the default. That framing is what makes this defect work. The question
that breaks it is "which writers does the Python default cover?" The answer
is "the ones that go through the ORM," and this repo has writers that do
not: `text()` inserts in tests and seed scripts, anything support runs by
hand, any future bulk load. After the second statement the column is
`NOT NULL` with no default, so all of those start failing with a constraint
violation on a column they have never heard of. The fix is to delete the
second statement, and it is worth saying why the first one is fine on its
own: Postgres 11 and later store a constant default in the catalog without
rewriting the table.

**The backfill runs inside the migration (Major).** `op.execute` with an
`UPDATE` over `orders` reads as thoughtful: the author noticed that existing
shipped orders would have a NULL `shipped_at` and filled it in. Three
consequences follow. It runs in the transaction `env.py` opened for the
revision, so the row locks are held for the whole deploy. It is not
resumable, because a failure halfway rolls the whole revision back. And it
makes the deploy time proportional to table size, which is the one property
you want a schema change never to have. Say the alternative concretely: keep
the column nullable, backfill from a script that commits in chunks. There is
also a correctness question worth raising: `updated_at` moves on cancel and
refund, so for a refunded order it is not the shipment time at all.

**`shipped_at` is naive in the migration (Minor).** The model says
`DateTime(timezone=True)`, the migration says `sa.DateTime()`. SQLite renders
both as `DATETIME`, so no test in this repo can see the difference; that is
exactly why it needs a reviewer. On Postgres the column becomes `timestamp
without time zone`, the aware value from `utcnow()` is written with its
offset stripped, and convention 7 is broken for every row. The secondary
effect is that `alembic revision --autogenerate` will propose this type
change forever, which trains everyone to ignore autogenerate output.

**The file name and the docstring header (Nits).** Neither changes behavior.
The file name breaks the sort order of the versions directory; the docstring
header says `Revision ID: 0002` and `Revises: 0001` while the variables are
correct. Say both in one line each and mark them non-blocking in the
summary. A review that spends as many words on a file name as on a downgrade
that destroys a column has told the author nothing about priority.

**`batch_alter_table` is fine (the clean trap).** It looks like the most
dangerous thing in the file, because on some dialects batch mode copies the
table into a new one. It does not do that here. `env.py` sets
`render_as_batch=True` so that migrations work on SQLite, and on Postgres
`batch_alter_table` with the default `recreate="auto"` emits plain
`ALTER TABLE` statements. Asking "does this recreate the table on Postgres?"
is a good question. Asserting that it does is a false positive, and in this
diff it would be the loudest comment on the page while the real table
rewrite, the backfill four lines below, went unmentioned.

## Design and tests

The follow-up commit adds an admin report on shipment transit time and, on
the way, replaces `tracking_number` with `tracking_id`. It reads as ordinary
finishing work, which is exactly why the four findings in it are easy to
walk past.

**The domain helper imports the ORM (Major).** `app/domain/shipping.py` is a
new file under `app/domain/`, and the habit that catches this finding is
checking the imports of any new file in that package before reading its
body, the same way you would check a function's return type before its
implementation. Line 7 imports `Order` from `app.db.models`. That single
import means `transit_days` cannot run, or be tested, without a live
session, which is the whole reason `app/domain/` exists as a layer: so
pricing, dates, and now transit time can be reasoned about and tested
without a database. The fix is one signature change, take `shipped_at`
directly instead of the row, because the caller already has the field on
hand.

**`ship()` reads the clock itself (Minor).** You notice this one by asking a
question you should ask of every function that writes a timestamp: how would
a test pin this value to something exact? `order.shipped_at = utcnow()`
answers that question badly. There is no parameter to substitute, so any
test can only assert a range around "now", never an exact instant, and the
existing `test_shipped_at_is_utc` does exactly that. Compare it with
`DateRange.last_n_days`, a few files over, which takes `today` as an
optional parameter for this exact reason.

**The day count re-implements `DateRange` (Refactor).** Any `while` loop that
walks dates one day at a time is worth a second look in a codebase that
already has a `DateRange` type with a `.days` property built for this. The
loop in `transit_days` is not wrong, it produces the same inclusive count
`DateRange(start, end).days` would, which is exactly what makes this a
refactor and not a defect: nothing breaks today, but the next person to read
the file has to convince themselves the hand-rolled loop agrees with
`DateRange`'s definition of "inclusive" instead of trusting a name they
already know.

**The boundary test skips the boundary (Major).** `ShipOrderRequest` caps
`tracking_id` at 64 characters. The habit that catches this is finding the
threshold in the schema first, then checking whether the matching test uses
a value near it. `test_tracking_id_is_bounded` sends 200 characters, which
proves the field rejects something absurdly long but says nothing about
whether 64 is accepted and 65 is not. An off-by-one in the limit would pass
this test either way.

Two questions an interviewer might ask about these:

1. The domain layer rule says nothing under `app/domain/` may import
   `app.db`, `app.services`, or `app.api`. Why does that rule exist for a
   read-only helper like `transit_days`, which never writes anything? What
   would go wrong in a year if this import were left in place?
2. `test_tracking_id_is_bounded` passes both before and after the fix,
   because there is no actual bug in the 64 character limit. Given that,
   what is the argument for spending review time on a test that already
   passes, and what would change your answer if this were a payment amount
   instead of a tracking id?

## Questions to ask the author

- What is the rollback plan for this revision? The answer tells you whether
  the downgrade was ever read, let alone run.
- Where did `updated_at` come from as the estimate for `shipped_at`? Is a
  refunded order's `updated_at` acceptable as its shipment time?
- How long does the backfill take against a copy of production? If the
  answer is "I have not run it", that is the finding.
- Who writes to `orders` without going through the ORM today? Seed scripts
  and support tooling both count.
- Should `tracking_number` be empty string or NULL when an order ships
  untracked? The PR picked empty string; that choice belongs in the
  description.

## Five questions an interviewer would ask about the rewrite

1. You kept `server_default=""` on `tracking_number` rather than making the
   column nullable. Argue the other side. What does an empty string cost a
   reader six months from now who cannot tell "shipped untracked" from
   "not shipped yet"?
2. You removed the backfill entirely instead of moving it into the same PR
   as a script. What is the deploy sequence you would actually run, and what
   does the system look like between the migration landing and the backfill
   finishing?
3. The downgrade fix is one word. How would you catch this class of mistake
   without a reviewer, given that the existing migration test downgrades to
   `base` and therefore cannot see it?
4. `shipped_at` with `timezone=True` is invisible on SQLite and load bearing
   on Postgres. Name two other things about this schema that the SQLite test
   suite cannot verify, and say what you would do about them.
5. This revision is safe to run online. Suppose the next one has to add an
   index to `orders` instead. Walk through what changes about the migration,
   the deploy, and the review, and where `autocommit_block` comes into it.
