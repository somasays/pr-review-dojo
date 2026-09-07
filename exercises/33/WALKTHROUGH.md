# Exercise 33 walkthrough: loyalty credit at checkout

Mode: teach. Domain: services. Difficulty: easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/76
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/78

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

This PR adds a new pure domain rule and one new step in an existing method.
That shape tells you where to start: the entry point that decides whether
the rule runs at all, then the rule itself, leaf first.

1. **Read the PR description, then the file list.** Seven files:
   `app/domain/loyalty.py` (new), `app/services/order_service.py`,
   `app/services/notification.py`, `app/db/repositories.py`,
   `app/db/models.py`, `app/db/alembic/versions/0003_...py`,
   `app/api/schemas.py`, and two test files. The migration and the schema
   field are the cheapest to verify (one column, one field) so leave them
   for last.
2. **Open `app/services/order_service.py` and read `create` top to bottom.**
   This is the composer: it looks up the customer's paid history, calls the
   domain rule, applies the credit to the taxable amount, builds the order,
   and conditionally notifies. Everything downstream is either a leaf this
   method calls or a caller this method's contract has to satisfy.
3. **Then `app/domain/loyalty.py`,** the leaf the new step depends on. This
   is the smallest file in the diff and where the domain-purity and
   boundary issues live. Read the module docstring first, it makes a
   promise ("pure functions over `Money`"), then check whether the imports
   and the function signature keep it.
4. **Then `app/db/repositories.py`,** specifically the one new method. It
   exists only to feed `loyalty.py`, so read it against what `loyalty.py`
   actually needs.
5. **Then `app/services/notification.py`,** the new `loyalty_credit_applied`
   method, next to its three siblings in the same file for comparison.
6. **Then `cancel` in `order_service.py`.** It is easy to miss because the
   feature described in the PR title does not obviously touch it; the tell
   that it changed is a `git diff` on a method the description never
   mentions.
7. **Then the tests, the schema field, and the migration.** By now you know
   what the rule does, so you are checking whether the tests would catch a
   wrong answer and whether the new column and field are wired correctly.

## 2. What to grep for before commenting

- `import` in `app/domain/`. This package's own docstrings promise no IO, no
  database. One file breaks that promise, and grep finds it in one line.
- `dedupe_key` in `app/services/`. Four call sites now use
  `f"order-<event>:{order_id}"`; one call site does not match the pattern.
- `.percent(` versus `* rate / Decimal(100)` (or similar) in
  `app/domain/`. `Money.percent` exists; a hand-rolled version next to it
  is worth a comment even when it produces the right number.
- `< ` and `<=` in `app/domain/loyalty.py`. There are two strict-looking
  comparisons in this file. Check each one against what happens exactly at
  the boundary; they resolve differently.
- `stock +=` in `app/services/order_service.py`. One place restores stock.
  Trace what has to be true before that line runs, twice.
- `500.00`, `2000.00` in `tests/`. The tier thresholds the feature
  advertises. If neither number appears as a test input, the boundary is
  untested.

## 3. The reasoning chain that surfaces each finding

**The replayed cancel (Blocker).** Ask the idempotency question the README
forces on every write: what happens when `cancel` runs twice on the same
order? Trace it against the diff, not just the final code: the
already-cancelled check used to be the very first line and returned before
anything else ran. Now it is folded into the same condition as the
transition check, and the restock loop sits between that condition and the
actual `return`. A first call cancels normally. A second call (a retried
request, a double click) evaluates the same condition, still finds nothing
to raise, and runs the restock loop again before returning. The order ends
up in the same state either way, so the response looks correct; the only
trace is a shelf count that grew. That is what makes it a Blocker: nothing
in the response tells you it happened.

**The tier boundary (Major).** `app/domain/loyalty.py`'s own comment
advertises "(minimum lifetime spend, credit rate) pairs". Advertising a
minimum implies that landing on it qualifies. Read `_rate_for`: the
comparison is `minimum < lifetime_spend`, strict. A customer at exactly
2000.00 fails that check for the top tier and falls through to check 500.00,
which they pass, landing on 2 percent instead of 5. Round numbers like 500
and 2000 are not edge cases here, they are the values the feature is
advertising, so this is wrong under realistic input, not a hypothetical.

**The notification dedupe key (Major).** Read the module docstring of
`notification.py`: every message carries a dedupe key so a retry after a
partial success does not double send. The three existing methods honor
that with a stable key built only from the order id. The new
`loyalty_credit_applied` appends `utcnow().isoformat()`. No two calls ever
produce the same key, so the one property the docstring promises does not
hold for this message, and a worker retry or a gateway-level replay emails
the customer twice.

**The domain layer importing the ORM model (Major, design).** The module
docstring says "pure functions over `Money`". The first import is
`from app.db.models import Order`, and the function signature takes
`paid_orders: Sequence[Order]`. That is a direct contradiction between what
the file claims and what it does, and it costs something concrete: this
function cannot be unit tested without constructing real `Order` rows, and
nothing outside a SQLAlchemy session can call it. The fix moves the
aggregation into the repository (`paid_total_for_customer`, one `SUM`
query) and has the domain function take the already-computed `Money`.

**The hand-rolled percent (Minor, design).** `Money.percent` exists one
file away and does exactly this: multiply by the rate, divide by 100, let
the constructor round. The new code repeats that arithmetic inline instead
of calling it. It produces the right number today because `Money`'s
constructor re-quantizes regardless, so this is a design comment, not a
correctness one, worth exactly a "use the helper" and nothing more urgent.

**The unused `tiers` parameter (Minor, refactor).** `loyalty_credit` takes
a `tiers` parameter that lets a caller override the table. `OrderService`
is the only caller, and it never passes one. A parameter that always takes
its default is a promise the code makes to a caller that does not exist
yet; the promise is what costs a reader something, not the one extra
argument.

**The missing boundary test (Major, test).** The shipped tests use lifetime
spends of 1000, 3000, and 5000. None of them is 500.00 or 2000.00, the two
values the tiering is defined in terms of. A tiered rule is defined by its
boundaries, and this is exactly the case that would have caught the strict
`<` above before it shipped.

## 4. Design and tests

The design findings here are not independent of each other: DS-06 (the ORM
import) is the root cause, and fixing it changes `loyalty_credit`'s
signature, which is why the DS-08 and DS-17 fixes land in the same
function once DS-06 is addressed. When you review a small pure-logic
module, check its imports before you check its logic; an import from
`app.db` or `app.services` inside `app/domain` is a five-second check that
tells you the function's test story before you read a single branch.

The test finding here is a coaching opportunity as much as a gap: the
shipped tests read as thorough (five cases, a new-customer case, a capped
case) but never hit the two numbers the feature is defined against. A
tiered or thresholded rule is only as tested as its boundaries; count of
test cases is not the same as coverage of the cases that matter.

## 5. Questions worth asking the author

- What happens if `create` is called twice with the same idempotency key
  after a credit was applied? (Answer: the early return at the top of
  `create` means the credit is computed once, on the winning call, so this
  is safe; worth confirming out loud rather than assuming.)
- Why does `cancel` log the loyalty credit at all? Is that log line used
  by anything downstream, or is it just for support, and if it's for
  support, should it be structured rather than an f-string?
- The credit is applied at order creation, before payment. What happens to
  a customer's lifetime spend calculation, and therefore their tier, if
  they create an order with a large credit and then never pay for it? Walk
  through whether `paid_total_for_customer` (only `PAID` orders) already
  handles this correctly.
- Are the tier thresholds and the cap meant to be the same for every
  currency, given they are literal `Money.of("500.00")` style constants?
- The `OrderOut` schema now exposes `loyalty_credit`. Is a customer meant to
  see how they qualified for it (their tier, their lifetime spend), or just
  the resulting number?

## 6. Five interviewer questions about the rewrite

1. The fix for `cancel` moves three lines back to where they started. Argue
   for and against a more defensive alternative: guarding the restock loop
   itself with `if not already_cancelled`, so the ordering mistake can't
   recur even if someone reorders the method again later. What does each
   approach cost the next person who edits this method?
2. The domain-purity fix changes `loyalty_credit`'s parameter from a
   sequence of ORM rows to a single `Money`. What is the smallest test you
   can no longer write with the old signature that you can write with the
   new one, and what does that test buy you?
3. `paid_total_for_customer` does a `SUM` in SQL where the original code
   fetched every row and summed in Python. For a customer with ten thousand
   paid orders, what changes about this query's cost, and would you have
   caught that difference in review without running it?
4. The rewrite drops the `tiers` parameter as unused. Under what
   circumstance would you push back on that and ask the author to keep it?
5. None of the six commits in the rewrite introduce a new class or a new
   abstraction. Pick the one you were most tempted to over-build (a
   strategy object for the tiers, an idempotency table for the dedupe key,
   a value object for lifetime spend) and explain what it would have cost
   the next reader.
