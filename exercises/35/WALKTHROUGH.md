# Exercise 35 walkthrough: flash sale windows

Mode: teach. Domain: concurrency. Difficulty: medium.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/82
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/84

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

This PR touches four layers for one feature: a pure pricing rule, an
in-process counter with a background thread, a checkout step, and an admin
endpoint. Read leaves before the composer: understand the two building
blocks in isolation before you read the code that wires them together.

1. **Read the PR description and the file list, not the code.** Two new
   files (`app/domain/flash_sale.py`, `app/services/flash_sales.py`), four
   touched files, one new test file. The new files are where the actual
   logic lives; the touched files mostly wire it in.
2. **Open `app/domain/flash_sale.py` first.** It is the smallest, most
   self-contained piece: one dataclass, three methods, no IO. Read
   `is_active`, `sale_price`, and `units_within_cap` and work out by hand
   what each should return at its boundary: the instant the window opens
   and closes, the exact quantity that lands on the cap, a percent-off deep
   enough to hit the floor. This is a leaf; get it right before anything
   that calls it.
3. **Then `app/services/flash_sales.py`, the counter component, on its
   own, ignoring who calls it.** `SaleCounter` is the shared, mutable piece
   of this PR: two dicts, one lock, a background thread. Ask the standard
   concurrency questions before reading a single caller: which methods
   touch `_sold` and `_sold_by_customer`, which of those hold `self._lock`,
   and does every mutating method hold it. Then look at `start`/`stop` and
   the module-level `ACTIVE_SALES` table separately from the counter class;
   they are two different pieces of state with two different lifetimes.
4. **Then `app/api/deps.py`, `get_sale_counter`.** This is the composer for
   the counter: it decides when exactly one `SaleCounter` gets built and
   started for the whole process. Compare it to `_sender = InMemorySender()`
   two lines above it, which is built at import time, not during a request.
   Anything built lazily, during a request, from a threadpool, needs to
   answer "what happens if two requests get here at the same time on a cold
   process."
5. **Then `app/services/order_service.py`, `_flash_sale_prices` and
   `_check_sale_cap`, read against `create`.** This is the checkout step:
   it decides the price for each line and is supposed to enforce the cap.
   Read the `try`/`except` around the cap check slowly, line by line, and
   ask what runs after the `except` block regardless of whether it fired.
6. **Then `app/api/routers/reports.py`, `active_sales`.** By now you know
   what the counter tracks; this handler just reads it back out.
7. **Then `tests/test_flash_sale.py`.** By now you know what could go
   wrong; check whether the shipped tests would actually catch it.

## 2. What to grep for before commenting

- `self._lock` in `app/services/flash_sales.py`. Two methods reference it,
  `reset` and (arguably) `record_purchase`. Count how many of the methods
  that mutate `_sold` or `_sold_by_customer` actually acquire it.
- `except SaleCapExceeded` and `raise SaleCapExceeded`. One raise site, one
  except site, right next to each other in `_flash_sale_prices`. When a
  raise and its catch are three lines apart in the same function, read what
  happens after the `except` block, not just inside it.
- `_sale_counter` in `app/api/deps.py`. One `global`, one `is None` check,
  no lock in the exercise version. Compare it to `get_settings` in
  `app/services/config.py`, which is also a lazily built module-level
  value but is safe for a different reason (see the clean-code trap on a
  similar shape in exercise 15's answer key).
- `sale.ends_at` and `is_active` in `app/services/flash_sales.py`. The
  closer thread's loop and `FlashSale.is_active` both express "is this sale
  still running." Only one of them handles both ends of the window.
- `time.sleep` in `tests/`. One hit, in the new file, in a test about a
  background thread.
- `<` and `<=` in `app/domain/flash_sale.py`. Three comparisons in three
  methods; only one of them guards a cap, and cap boundaries are the
  single most common off-by-one in this codebase's history.

## 3. The reasoning chain that surfaces each finding

**The counter singleton (Blocker).** Ask the same question every lazy
singleton in this codebase needs answered: what happens if two request
threads call `get_sale_counter()` at the same time on a cold process? Both
read `_sale_counter is None` as true before either one assigns it, both
build a `SaleCounter`, both call `start()`. One of the two objects is the
one later callers actually get; the other is orphaned, its closer thread
still alive, its counts invisible to anyone else. The fix is the standard
one: a lock around the construct-and-start, with the `is None` check
repeated inside it.

**The swallowed cap check (Blocker).** This is the one finding you can
spot without knowing anything about concurrency: read `_flash_sale_prices`
as straight-line code, single threaded, no races involved. The `try` block
calls `_check_sale_cap`, which raises when the cap is exceeded. The
`except` block logs it. Then, unconditionally, two lines below the
`except`, `overrides[item.sku]` gets set to the sale price anyway. There is
no `continue`, no `return`, no re-raise. Whatever the cap check decided,
the price is applied. A PR whose entire point is "cap the sale price per
customer" ships with no cap.

**The cap boundary (Major).** `units_within_cap` returns
`already_purchased + quantity < self.per_customer_unit_cap`. Plug in the
number the feature is built around: a cap of 3, a customer who has bought
1, ordering 2 more. `1 + 2 < 3` is `False`. The purchase that exactly
matches the advertised limit is rejected. This is not an edge case for a
"3 per customer" sale; it is the input the feature will see constantly,
because customers optimizing for a limited sale try to buy exactly the
limit.

**The unlocked counter increment (Major).** `record_purchase` does
`self._sold[sku] = self._sold.get(sku, 0) + quantity` with no lock, even
though `self._lock` exists and `reset` uses it correctly three methods
below. Two concurrent checkouts for the same SKU can both read the same
starting count and both write back the same incremented value, losing one
of the two increments. This is quieter than the first two findings: it
does not throw, it does not obviously misbehave in a single-threaded test,
it just slowly undercounts under exactly the traffic a flash sale
produces, which also means the cap check upstream can pass for someone who
has actually already reached it, compounding the fix above.

**The three Minor findings.** `_flash_sale_prices` reads `utcnow()`
directly instead of taking `now` as a parameter, so nothing above it can
pin an exact instant to test window-edge behavior. `active_sales` builds
its response row inline with no pure formatting step. The closer thread
hand-checks `now > sale.ends_at` instead of calling the `is_active` method
that already exists and covers both ends of the window, a smaller version
of the same "two places, one rule" risk as the swallowed exception.

**The shipped test (Minor).** `test_sale_counter_stops_its_background_thread`
sleeps for 0.1 seconds before calling `stop()`. `stop()` already calls
`join(timeout=2)`, which is the deterministic wait; the sleep in front of
it proves nothing and would not catch a `start()` that silently failed to
spawn a thread.

## 4. The clean-code trap

`ACTIVE_SALES` in `app/services/flash_sales.py` is a module-level dict of
`FlashSale` instances, hardcoded. It looks like business data that belongs
in a database, and it is fine as code: it is the same shape as
`TAX_RATES` in `app/domain/pricing.py` and `DISCOUNT_CODES` in
`app/services/pricing_service.py`, both already established patterns in
this codebase, both tested, both changed by a deploy rather than a support
ticket. A database-backed version is a real feature (a migration, a cache,
an admin CRUD surface) that nothing in this PR asks for yet. Asserting it
must move to the database is a false positive; asking whether more than a
handful of concurrent sales is expected soon is a fair question that might
change the answer later.

## 5. Design and tests

Three findings here are not bugs; the code works as written, and a strong
reviewer would still ask for the change.

- **The hidden clock** (`order_service.py`, `_flash_sale_prices`): the tell
  is a bare `utcnow()` call inside a method whose whole job is to reason
  about a time window. Nothing above it can hand in a fixed instant, so a
  test that wants to check behavior exactly at a sale's `ends_at` has to
  patch the clock for the entire process instead of passing a value.
- **Formatting mixed into the handler** (`reports.py`, `active_sales`): the
  tell is a response model built inline inside a FastAPI handler, with no
  step that can be called with plain values. The next field added to this
  report means editing the handler and re-reading the whole loop.
- **The duplicated window check** (`flash_sales.py`, the closer thread's
  `_run`): the tell is a hand-written comparison, `now > sale.ends_at`,
  sitting in the same file as a method, `is_active`, that already expresses
  the same idea more completely. Two places encoding one rule will drift
  the next time the rule changes.
- **The threading test** (`test_sale_counter_stops_its_background_thread`):
  the tell is `time.sleep` immediately before a call, `stop()`, that
  already performs a deterministic wait. A sleep in a concurrency test is
  worth a second look every time; ask what the test is actually
  synchronizing on.

Two questions about them:

1. If `_flash_sale_prices` took `now` as a parameter, would you want
   `create` itself to accept one too, so a test can freeze the whole
   checkout at a fixed instant, or is one layer of clock injection enough?
2. The closer thread and `is_active` express the sale window rule twice.
   If the rule later needs to become half-open (active up to but not
   including `ends_at`, to avoid a boundary tie with the cap check), how
   many places in this PR would need to change, and does today's diff make
   that change easy or hard?

## 6. Questions worth asking the author

- What happens if two customers who have each already bought 2 of a
  3-per-customer item both check out at the same instant? (This question
  alone surfaces both Blockers without reading a line of code.)
- Right now a sale applies automatically to every order for its SKU with
  no opt-in. Is that the intended UX, or should a customer see the sale
  price before committing to it?
- The per-customer cap is tracked in process memory. What happens to it on
  a deploy, and is losing the count (letting everyone's cap reset) an
  acceptable cost, or does it need to survive a restart the way the
  reservation cache from an earlier exercise persists to disk?
- `ACTIVE_SALES` has one entry. Is more than one flash sale running at once
  expected soon, and if so, does anything about the admin endpoint's
  response shape need to change to stay useful at that scale?
- The shipped tests cover the pricing math and one happy-path checkout.
  Which of the two Blockers would they have caught if the fix regressed
  again next month?

## 7. Five interviewer questions about the rewrite

1. The fix for the counter singleton adds a second `is None` check inside
   the lock. Why is the outer check, the one before the lock, still worth
   keeping once the inner one exists?
2. The fix for the swallowed exception removes a `try`/`except` entirely
   rather than adding a `return` or `continue` inside it. What would each
   of those alternatives have done differently to the caller, and why is
   letting it propagate the better shape here?
3. `units_within_cap` changed one character. Walk through why a reviewer
   should still write a hidden test for a one-character fix instead of
   trusting the diff is obviously correct.
4. The counter's lock change wraps two dict updates in one `with` block
   instead of giving each dict its own lock. Under what conditions would
   two locks actually be the right call, and does anything about
   `SaleCounter`'s shape rule that out here?
5. None of the eight fixes added a new class, a new module, or a new
   abstraction. Pick the one you were most tempted to over-build (a
   `Lazy[T]` helper, a `Clock` protocol, a `WindowPolicy` type) and explain
   what it would have cost the next reader compared to what shipped.
