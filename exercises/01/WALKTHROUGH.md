# Exercise 01 walkthrough: order notes

Mode: teach. Domain: fastapi. Difficulty: easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/1
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/7
- Feature: `PATCH /orders/{order_id}/notes` appends a free-text note to an
  order, and `OrderOut` gains a `notes` list.

Read the exercise PR with its inline comments first, then the rewrite PR with
its per-hunk commentary, then this file.

## How to read this diff

The PR touches six files. Read them in this order, not in the order GitHub
lists them.

1. **`app/db/models.py`.** The schema tells you what the feature really is.
   A new table with a foreign key to `orders`, a new relationship on `Order`.
   The question to carry forward: what loading strategy does this
   relationship use, and does the response model that reads it care.
2. **`app/db/alembic/versions/0003_order_notes.py`.** Check that the
   migration matches the model: same columns, same types, same nullability,
   an index where the model declares one.
3. **`app/api/schemas.py`.** This is the contract. For a request model, ask
   what a hostile client can put in each field. For a response model, ask
   what a curious client can read out of it.
4. **`app/api/deps.py`.** Small diff, easy to skim past. `get_db` is the one
   function every request in this API depends on, so a change here is never
   local to the feature that happened to touch it.
5. **`app/api/routers/orders.py`.** Now read the handler. Compare it to
   `get_order` right above it and to `cancel_order` below it: same file, same
   kind of write, different shape.
6. **`tests/test_api_order_notes.py`.** Read the tests last and read them for
   what they do not cover. Tests tell you what the author was thinking about.
   The gaps tell you what they were not.

## What to grep for

- `commit(` across `app/api/` and `app/db/`. The only commit in the request
  path should be the one in `get_db`. Then read what `get_db` actually does,
  not what its name promises.
- `lazy=` in `app/db/models.py`. `Order.items` says `lazy="selectin"`, the
  new `Order.notes` says nothing. Whenever a diff adds a relationship and a
  response field in the same change, the loading strategy is a question.
- `except` in `app/api/routers/orders.py`. Every handler that can raise
  `InvalidTransition` needs to catch it. Count the handlers against the count
  of `except InvalidTransition` blocks.
- `is_admin` in `app/api/routers/orders.py`. It appears in more than one
  handler. Read both appearances side by side before deciding whether that is
  two features or one feature typed twice.

## The reasoning chain for each finding

**`get_db` stops committing (Blocker).** This is the one finding in this
exercise that is not in the diff you would naturally scroll to, it is a
five-line change in a file the feature only touches in passing. The tell is
in the diff itself: a five-line try/except/finally collapsed into a two-line
`with` block. Whenever a diff simplifies a resource-management block, ask
what the original block was doing besides acquiring and releasing the
resource. Here it was also committing. `Session.__exit__` closes the
connection, it does not commit, so the shorter version is not equivalent to
the longer one, it changes behavior for every write in the API. It survives
the exercise's own test suite because `tests/conftest.py`'s `client` fixture
overrides `get_db` with its own version that still commits, so nothing in
this repository's CI would ever catch it. The lesson: a diff to
infrastructure code that nothing in the feature's own tests exercises is the
diff that most needs a second look, not less of one.

**A closed order returns 500 instead of 409 (Major).** Read the terminal
check next to the `try/except` around it, not inside it. The check calls
`transition()`, the same function `cancel_order` uses to raise
`InvalidTransition` for an invalid move, so it can raise here too. Then look
at what the surrounding `except` clauses catch: only `NotFound`. Every other
write endpoint in this file, `cancel_order`, `pay_order`, `ship_order`,
catches `InvalidTransition` and maps it to a 409. This one does not, so the
one input that reaches that branch, a note on a cancelled or refunded order,
raises straight through the handler. The way to catch this class of bug is
to notice a new call to a function you have seen raise before, and check
that every place it is called new is guarded the way every place it is
called elsewhere already is.

**Notes load one query per order (Major).** Nobody writes an N+1 on purpose.
It appears when two safe-looking changes meet: a relationship is added with
default settings, and a response model grows a field that reads it. Neither
line is wrong alone. The way to catch it is to ask, for every field added to
a response model, what happens when this endpoint returns the maximum page
size. `PAGE_SIZE_MAX` is 200, so the answer here is 200 extra queries on an
endpoint that ran two before. The tell is one line above: `items` is
`lazy="selectin"` because someone already fought this fight.

Contrast this with the line right below the write, `return order`, which
hands an ORM `Order` to `response_model=OrderOut`. That is the thing on this
PR that looks wrong and is fine, and it is the false positive this exercise
is built to catch. `OrderOut` sets `from_attributes=True`, is the allowlist
written for order responses, and omits `customer_id`, `idempotency_key`, and
`updated_at`, so nothing leaves the process that was not listed. Convention 9
forbids returning a row through a model that was not written for the
endpoint, not returning rows at all. Asking "do we want notes on every order
response, or only on the detail?" is a fair question; asserting that this
leaks the row is a minus five.

The lesson from the pair: the lazy strategy, not the shape of the code,
decides what a relationship costs, and the response model, not the type of
the returned object, decides what a response exposes.

## Design and tests

A strong reviewer keeps reading past "does it work" into "will the next
change to this file be easy or painful." Two structural comments and one
opportunity fall out of that question here, plus one gap in the test itself.

**The handler bypasses `OrderService` (Major, design).** `create_order`,
`cancel_order`, `pay_order`, and `ship_order` all take a `service: Orders`
parameter and do their writes through it. `add_order_note` does not: it
builds the note row and writes it through `OrderRepository` directly, in the
handler. You notice this the same way you notice a missing import, by
pattern-matching against the four sibling functions in the same file before
you read the fifth one closely. The consequence is not that this specific
write is wrong today, the consequence is that the transition rule (no notes
on a closed order) now lives in the router instead of next to `cancel`,
`ship`, and `pay` in the service, so the next person who needs that rule from
a worker or a script has nowhere to call it from without duplicating it.

**The ownership branch is copied, not shared (Minor, design).** `get_order`
and `add_order_note` both have the identical four lines: if the principal is
an admin, `repo.get(order_id)`, otherwise `repo.get_for_customer(order_id,
principal.customer)`. You catch this by literally comparing the two
functions, which is worth doing whenever a new handler needs the same kind of
lookup an existing handler already has. Two copies is not a bug by itself,
today they say the same thing. It becomes a bug the day someone changes the
ownership rule in one copy, for a good reason specific to that endpoint, and
forgets the other one exists.

**The author label is a refactor opportunity, not a defect (Minor,
refactor).** `author = "admin" if principal.is_admin else
f"customer:{principal.customer}"` sits between the lookup and the write. It
is correct, it is short, and nobody should block a merge on it. The reason a
strong reviewer still mentions it: it is a piece of string formatting living
inside a function that also does authorization and persistence, and pulling
it into a one-line named function costs nothing and makes the handler read
as a sequence of steps. The signal that separates a refactor comment from a
design comment is exactly this: would you approve the PR as it stands. Here
the answer is yes.

**The length test never reaches the boundary it is testing (Major, test).**
`OrderNoteIn.body` is bound to 500 characters, matching the `String(500)`
column. The shipped test checks a 10-character body and an 800-character
body. Read every test that exercises a bound (a max length, a page size cap,
a day count) and ask what value it actually passes, not what the test's name
claims to check. A test with the right idea and the wrong numbers passes
today and stays green through an off-by-one in the code it is supposed to
guard.

Two questions an interviewer might ask about these four:

1. The design comment says the handler should go through `OrderService`. If
   `add_order_note` needs to stay a one-liner and the service is for writes
   that also send notifications or touch stock, is the layering violation
   still worth blocking on? What would change your answer?
2. You have a refactor comment and a design comment that both live in the
   same handler. What separates a comment you would block a merge on from
   one you would leave as "worth doing sometime"? Point at the exact
   difference between the ownership-branch comment and the author-label
   comment.

## Questions to ask the author

- What happens to notes when an order is cancelled or refunded? The terminal
  check in this diff answers "nothing, it is rejected", but nothing in the
  PR description says that was a deliberate choice.
- Is `author` meant to be readable by the customer? `customer:<id>` and
  `admin` are both visible in the response today.
- Do you expect notes to be edited or deleted later? If yes, the endpoint
  should return the note with its own id, which argues for POST.

## Five questions an interviewer would ask about the rewrite

1. The session-dependency fix restores five lines that a two-line version
   replaced. How would you explain to a teammate why the shorter version was
   wrong without them running the code, just from reading it?
2. You fixed the N+1 with `lazy="selectin"` on the relationship rather than
   `selectinload` in the repository. When would you make the opposite choice,
   and what does each cost the caller who does not want notes?
3. Moving the write into `OrderService.add_note` also moved the terminal
   check with it. What would you lose if you had fixed only the 409 mapping
   in the router and left the write where it was?
4. The rewrite adds `_scope_order`, shared by two call sites. At what number
   of call sites does an extraction like this stop paying for itself, and
   what would the third caller need to look like for the shared helper to no
   longer be the right shape?
5. The hidden test for the N+1 counts SQL statements and asserts at most
   five. What is fragile about that test, and what would you assert instead
   if this endpoint were on a hot path you had to defend for a year?
