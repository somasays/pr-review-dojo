# Exercise 03 walkthrough: order refunds with stock reversal

Mode: teach. Domain: services. Difficulty: easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/3
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/9

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

A refund is a money movement, an inventory movement, and a customer email in
one method. That tells you where to look before you open a single file.

1. **Read the PR description and the file list, not the code.** Six files:
   `order_service.py`, `notification.py`, `retry.py`, one router, one schema
   module, three test files. The router and the schemas are the cheapest to
   verify, so leave them for last. `retry.py` is shared by everything that
   sends, so a change there is the highest blast radius line in the diff
   even though it is one line long.
2. **Open `app/services/order_service.py` and read `refund` against
   `cancel`.** They are the same shape: load, check the state, put stock
   back, move the status, notify. Reading the new method beside the existing
   one is faster than reading it alone, because any difference between the
   two is either an intentional improvement or the defect.
3. **Then `refund_lines`,** because the refund email quotes numbers to a
   customer and this is the only new arithmetic in the PR.
4. **Then `notification.py`,** first `_deliver` because every message goes
   through it, then `order_refunded`.
5. **Then `retry.py`.**
6. **Then the router, the schemas, and the tests.** By now you know what the
   service does, so you are only checking that the HTTP layer maps errors and
   that the tests cover the risky paths. They will not.

## 2. What to grep for before commenting

- `dedupe_key` in `app/services/`. Four call sites, three of them
  `order-<event>:{order_id}` and one with a timestamp in it. A convention
  that holds three out of four times is a defect, not a style choice.
- `stock +=` and `stock -=`. Two places add stock back (`cancel`, `refund`),
  one takes it away (`create`). Every one of them has to be idempotent.
- `commit(` in `app/services/` and `app/db/`. Zero hits is the expected
  answer here; see the clean trap below.
- `float(` in `app/`. It should appear nowhere near money.
- `except Exception` in `app/services/`.
- `README.md` conventions 3, 8, and 10. This PR touches all three.

## 3. The reasoning chain that surfaces each finding

**The double refund (Blocker).** Ask the idempotency question the README
forces on every write: what happens when this runs twice? Trace it. Second
call, `current` is `refunded`, so `is_refundable` is false but the guard
above short circuits on the status, so no exception. Then the loop runs and
adds every quantity back a second time. Then the guard below returns before
`_move` and before the email. So the reply looks identical, the email is not
duplicated, and the only trace is inventory that grew. That last part is what
makes it a Blocker rather than a Major: nothing surfaces it, and the shop
oversells days later. The tell is structural, and you can see it without
tracing at all: `cancel` returns before touching stock, `refund` returns
after. When two sibling methods disagree about ordering, one of them is
wrong.

**The dedupe key (Major).** Read the module docstring of
`notification.py`: every message carries a dedupe key so a retry after a
partial success does not double send. Now read the new key. It contains
`utcnow().isoformat()`, so no two calls ever share it. The retry inside
`_deliver` is what makes this concrete: a gateway that accepted the message
and then timed out gets a second call with a fresh key, and the customer
gets two refund emails. The severity is Major rather than Blocker because
the money is right and the damage is one confusing email, not corrupted
data.

**The discount split (Major).** Any time you see money divided by a count,
ask whether the parts add up. `Money` quantizes in `__post_init__`, so each
share rounds on its own and the sum drifts. Then look for the tool that
already exists: `Money.allocate` is right there in `app/domain/money.py`
with a docstring saying it splits into parts that sum exactly. A reviewer
who knows the codebase spots this as reinvention before they spot it as a
rounding bug.

**The broad except (Minor).** `except Exception` around a call that already
has its own failure type is worth a comment on its own, and the missing
`from exc` doubles it. Say both consequences: a `TypeError` in a sender is
reported to the client as a gateway problem, and the cause is gone from the
traceback. Minor rather than Major because it degrades debugging rather than
behavior.

**The float format (Nit) and the retry log (Nit).** These are cheap to spot
and cheap to fix. Group them at the end of your review and label them as non
blocking. A reviewer who leads with these has buried the Blocker.

## 4. The clean trap

`self._move(order, OrderStatus.REFUNDED)` flushes and no service method
commits. That looks like a missing commit and it is not: convention 8 gives
the transaction boundary to the caller, `get_db` commits on a successful
request and rolls back otherwise, and the flush makes the new status visible
to `refund_lines` in the same transaction. Asserting "this is never
persisted, add a commit" is a false positive, and the commit it asks for is
itself a known defect (it breaks the caller's rollback). Asking "who commits
this, `get_db`?" costs you nothing and is a fair question.

## 5. Questions worth asking the author

- What happens if this endpoint is called twice? (This is the question that
  finds the Blocker without any code reading at all.)
- Where does the money for the refund actually move? This PR emails the
  customer and reverses stock but never touches a payment gateway. Is that
  a follow up, and does the email overstate what happened?
- Should a refund put stock back at all? A returned item may be damaged. If
  the answer is "yes for now", that is a product decision worth a comment in
  the code.
- Who is allowed to refund, and is `reason` recorded anywhere other than an
  email body a customer receives?
- The three new tests cover one refund each. Which of the two most expensive
  failure modes, a replay and a discount that does not divide evenly, would
  they catch?

## 6. Five interviewer questions about the rewrite

1. The fix for the replayed refund moves three lines. Argue for and against
   the larger alternative, a `refunded_at` column with a unique constraint.
   What does each one buy you when two admin clicks race in different
   transactions?
2. `Money.allocate` gives the remainder cents to the first buckets. Is that
   the right policy for a refund breakdown, and who would notice if it were
   the last buckets instead?
3. The rewrite narrows `except Exception` to `except RetryExhausted`. What
   now happens to a `ConnectionError` raised by a sender on a policy whose
   `retry_on` does not include it, and is that the behavior you want at the
   API boundary?
4. Every commit in the rewrite is small enough to revert on its own. Which
   of the six would you be most comfortable shipping on a Friday, and which
   one would you want a hidden test for before it goes out?
5. None of the six fixes added a new class, a new module, or a new
   abstraction. Pick the one where you were most tempted to add one and
   explain what it would have cost the next reader.
