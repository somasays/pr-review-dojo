# Exercise 01 walkthrough: order notes

Mode: teach. Domain: fastapi. Difficulty: easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/1
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/7
- Feature: `PATCH /orders/{order_id}/notes` appends a free-text note to an
  order, and `OrderOut` gains a `notes` list.

Read the exercise PR with its inline comments first, then the rewrite PR with
its per-hunk commentary, then this file.

## How to read this diff

The PR touches five files. Read them in this order, not in the order GitHub
lists them.

1. **`app/db/models.py`.** The schema tells you what the feature really is.
   A new table with a foreign key to `orders`, a new relationship on `Order`.
   Two questions before you move on: who can write rows to this table, and
   who reads them. Both answers live in other files, so carry the questions
   with you.
2. **`app/db/alembic/versions/0003_order_notes.py`.** Check that the
   migration matches the model: same columns, same types, same nullability,
   an index where the model declares one. A migration that drifts from the
   model is a production incident that no test in this repo will catch.
3. **`app/api/schemas.py`.** This is the contract. For a request model, ask
   what a hostile client can put in each field. For a response model, ask
   what a curious client can read out of it.
4. **`app/api/routers/orders.py`.** Now read the handler, with the two
   questions from step 1 in hand.
5. **`app/db/repositories.py`.** Small, but confirm the new method follows
   the repository rules in this codebase.
6. **`tests/test_api_order_notes.py`.** Read the tests last and read them for
   what they do not cover. Tests tell you what the author was thinking about.
   The gaps tell you what they were not.

## What to grep for

- `get_for_customer` in `app/api/routers/`. Every route that takes an
  `order_id` from a customer key uses it. The new one does not. That single
  grep is the fastest path to the blocker in this diff.
- `lazy=` in `app/db/models.py`. `Order.items` says `lazy="selectin"`, the
  new `Order.notes` says nothing. Whenever a diff adds a relationship and a
  response field in the same change, the loading strategy is a question.
- `Field(` in `app/api/schemas.py`. Every string field in that module carries
  a bound except the one this PR adds.
- `commit(` across `app/api/` and `app/db/`. The only commit in the request
  path should be the one in `get_db`.
- `status.HTTP_` in the router. One bare integer in a file of constants.

## The reasoning chain for each finding

**The lookup is not scoped to the caller (Blocker).** The handler takes
`principal: CurrentPrincipal` and uses it, so at a glance authorization looks
handled. Look at what it is used for: building the `author` string. That is
attribution, not authorization. Then look at the lookup: `repo.get(order_id)`
is the admin path in `get_order`; the customer path there is
`get_for_customer`. So any customer key can write to any order id. Now finish
the thought that most reviewers stop short of: the handler returns the order
through `response_model=OrderOut`, so the attacker does not just write, they
read back the order's totals, discount code, and line items. A write bug that
is also a read bug moves to the top of the review.

This is the general shape worth remembering: a principal that is present and
used is not a principal that is checked.

**Notes load one query per order (Major).** Nobody writes an N+1 on purpose.
It appears when two safe-looking changes meet: a relationship is added with
default settings, and a response model grows a field that reads it. Neither
line is wrong alone. The way to catch it is to ask, for every field added to
a response model, what happens when this endpoint returns the maximum page
size. `PAGE_SIZE_MAX` is 200, so the answer here is 200 extra queries on an
endpoint that ran two before. The tell is one line above: `items` is
`lazy="selectin"` because someone already fought this fight.

Contrast this with the line right below it, `return order`, which hands an
ORM `Order` to `response_model=OrderOut`. That is the thing on this PR that
looks wrong and is fine, and it is the false positive this exercise is built
to catch. `OrderOut` sets `from_attributes=True`, is the allowlist written
for order responses, and omits `customer_id`, `idempotency_key`, and
`updated_at`, so nothing leaves the process that was not listed. Convention 9
forbids returning a row through a model that was not written for the
endpoint, not returning rows at all. Asking "do we want notes on every order
response, or only on the detail?" is a fair question; asserting that this
leaks the row is a minus five.

The lesson from the pair: the lazy strategy, not the shape of the code,
decides what a relationship costs, and the response model, not the type of
the returned object, decides what a response exposes.

**The note body is unbounded (Major).** The column is `String(500)`; the
Pydantic field is a bare `str`. Two failures fall out. An empty note is
accepted, so the notes list fills with blank rows and there is no delete
endpoint to remove them. And an over-long note is stored intact on SQLite,
which is what the tests run on, but raises at flush on Postgres, which is
what production runs on. Whenever a diff adds a validated input and a sized
column, read them side by side; a mismatch between the two is invisible to
every test in the suite.

**The handler commits (Minor).** `get_db` commits after the handler returns.
The handler commits too, so the request commits twice and the transaction
boundary quietly moved into the handler. Nothing breaks today. It breaks the
day someone adds a second write after this line, because the first half is
already committed. Convention 8 exists so that no handler author has to think
about this.

**The bare 404 and the docstring that promises 201 (Nits).** Neither changes
behavior. Say them once, say them briefly, and make sure the summary marks
them as non-blocking. A review that spends the same number of words on a
literal `404` as on an authorization hole has told the author nothing about
priority.

## Questions to ask the author

- Should a customer be able to add notes at all, or is this a support tool?
  The answer changes whether the endpoint needs a customer path.
- What happens to notes when an order is cancelled or refunded?
- Is `author` meant to be readable by the customer? `customer:<id>` and
  `admin` are both visible in the response today.
- Do you expect notes to be edited or deleted later? If yes, the endpoint
  should return the note with its own id, which argues for POST.

## Five questions an interviewer would ask about the rewrite

1. You fixed the N+1 with `lazy="selectin"` on the relationship rather than
   `selectinload` in the repository. When would you make the opposite choice,
   and what does each cost the caller who does not want notes?
2. The ownership fix returns 404 for an order that exists but belongs to
   someone else. Argue for 403, then argue against it. What does each choice
   leak, and which one matches the rest of this router?
3. You bounded the note body at 500 to match the column. If product comes
   back and asks for 5000 character notes, what changes, and in which order do
   you ship the model change, the migration, and the schema change so that no
   deploy window is broken?
4. Removing `db.commit()` from the handler changes nothing observable today.
   How would you convince a reviewer who says "it works either way, leave it"?
   What is the failure this prevents?
5. The hidden test for the N+1 counts SQL statements and asserts at most five.
   What is fragile about that test, and what would you assert instead if this
   endpoint were on a hot path you had to defend for a year?
