# Exercise 44 walkthrough: lost item reporting and loss reversal

Mode: teach. Domain: sandbox (library). Difficulty: medium.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/104
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/106

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

Start with the README, not the diff. `sandbox/library/README.md` states nine
conventions this codebase enforces, and this feature was built by a PR that
had to touch several of them at once: money, dates, transactions, and the
transition function. Reading the conventions first turns them into a
checklist you carry into the code, instead of something you notice only if
you happen to trip over it.

1. **Read the README's conventions 2, 4, 6, and 9 before opening a file.**
   Money is `Decimal`, quantized to cents, never an int (convention 2).
   Repositories never commit; the caller owns the transaction (convention
   4). Every loan status change goes through `transition()`, nothing sets
   `Loan.status` directly (convention 6). A librarian key can act for any
   patron; a patron key can only act for itself (convention 9). This
   feature can break any of the four, and each one gives you a boundary
   input to test before you have read a line of the diff:
   - Convention 2: does any money math round through something other than
     `Decimal.quantize(..., ROUND_HALF_UP)`? Build a case where the naive
     version and the correct version disagree by a cent.
   - Convention 4: does anything in `service.py` call `session.commit()`?
     If so, what happens if the call after it fails?
   - Convention 6: does every status change read `transition(...)`, or does
     something assign `loan.status = "..."` directly, or check it with a
     bare string?
   - Convention 9: does the "librarian may act for any patron" rule apply
     to both new endpoints, or did one of them forget it?
2. **Turn the PR description into inputs, not just prose.** "A replacement
   fee equal to the item's replacement cost plus any overdue fine accrued
   to the report date, capped by a configured maximum" is three numbers to
   pin down: the cost, the fine, and the cap, and one question, whether the
   cap applies to the sum or to a piece of it. "The item's copy count is
   reduced by one" is a before/after assertion on `item.copies`, and a
   question: is it reduced once no matter how many times you ask? "If the
   item is later found and returned within a configured window, a
   librarian may reverse the loss" is a boundary (the last day of the
   window) and a role check (librarian only).
3. **Find the entry point.** Two new routes in `sandbox/library/api.py`:
   `POST /loans/{loan_id}/report-lost` and `POST
   /loans/{loan_id}/reverse-loss`. Read their dependencies before their
   bodies. `report_lost` takes `identity: Identity`, any valid key.
   `reverse_loss` takes `_identity: Identity` too, the same as `report_lost`
   even though the feature description says only a librarian may reverse a
   loss. That mismatch is worth remembering before you go any further.
4. **Follow the call tree down.** `api.py` calls
   `LendingService.report_lost` and `LendingService.reverse_loss` in
   `service.py`. Both call into `domain/lending.py` (`fine_for`,
   `replacement_fee_for`, `transition`, `can_reverse_loss`) and
   `repo.py` (`ItemRepo.get`, `ItemRepo.available_copies`,
   `LoanRepo.get`).
5. **Read the leaves before the composer.** `domain/lending.py` has no IO
   and is the cheapest place to find an arithmetic mistake, so read
   `replacement_fee_for` and `can_reverse_loss` before you read
   `service.py`'s two new methods that call them. Once you know the leaves
   are correct (or not), reading the service methods is about sequencing
   and authorization, not arithmetic.
6. **`db.py`, `repo.py`, the tests, last.** `db.py` only adds two columns;
   skim it for the money type (`Numeric`, not `Float`) and move on.
   `repo.py`'s `available_copies` is one method, changed in one place.
   Read the shipped tests last, once you know what the feature promises,
   so you can tell what they are missing rather than just what they check.

## 2. What to grep for before commenting

- `session.commit(` in `sandbox/library/service.py`. Zero hits is the
  expected answer everywhere in this file except one.
- `LoanStatus.LOST.value` and `"lost"` in `service.py`. Four comparisons to
  the lost status exist across `service.py` and `repo.py`; if one of them
  spells it differently, that is the tell.
- `Identity` versus `Librarian` (or any librarian-only dependency) across
  the two new endpoints in `api.py`. If both use the same dependency, one
  of the two rules from the PR description ("who may report" versus "only
  a librarian may reverse") did not make it into the code.
- `item.copies -` and `item.copies +` in `service.py`. One decrements, one
  restores. Both should run exactly once per call, and neither should be
  followed or preceded by anything that changes what "once" means.
- `README.md` conventions 2, 4, 6, 9. This PR touches all four.

## 3. The reasoning chain that surfaces each finding

**The fee cap (Blocker).** The PR description promises a cap on "the item's
replacement cost plus any overdue fine accrued to the report date". That is
a cap on a sum. Now read `replacement_fee_for`: `capped_cost =
min(replacement_cost, cap)`, then the fine is added afterward. The cap was
applied to one of the two terms, not their sum. Pick a concrete case: a
$20 book, a $60 fine (near `FINE_CAP`), and a $75 replacement cap. The
correct answer is $75; the code returns $80. The tell does not require
tracing a caller at all, just comparing the docstring's promise ("never
exceeds `cap`") to the line above it.

**The premature commit (Blocker).** Convention 4 says services never
commit. Grep `session.commit(` in `service.py` and find exactly one hit, in
`report_lost`, sitting between `item.copies -= 1` and the `transition()`
call that is the only thing confirming the loan was actually active. Ask
the idempotency question every write in this codebase has to answer: what
happens if this runs twice? Trace it: the second call reads the
now-`lost` loan, decrements `copies` again, commits that decrement, and
only then does `transition(LOST, LOST)` raise. The caller sees an error,
but the copy is already gone a second time and nothing rolls that back,
because it was never inside the same transaction as the failure. The
structural tell: every other method in this service (`checkout`,
`return_item`, `place_hold`) mutates in memory and lets the caller commit
once. `report_lost` is the one method that commits partway through, and
that is reason enough to look hard at what runs after it.

**The double-counted availability (Major).** `available_copies` now
filters on `Loan.status.in_([ACTIVE, LOST])` instead of `ACTIVE` alone.
Ask what `item.copies` already means: the PR description says a lost
report reduces it by one, permanently. So a lost loan is already priced
into `item.copies` before this query runs. Counting it again here is
double-subtracting the same loss. Concretely: one copy, checked out, then
reported lost. `item.copies` drops from 1 to 0. The old query (active
only) would now read `0 - 0 = 0`, correct: nothing is available and
nothing ever will be again for that copy. This query reads `0 - 1 = -1`.
Availability that goes negative is the smell that gets you here even
without knowing the history: no other query in this codebase can produce a
negative count from non-negative inputs.

**The missing role gate (Major).** Read `reverse_loss`'s signature:
`_identity: Identity`. Read `require_patron` a few lines above it in the
same file, the pattern this codebase already uses to gate an endpoint by
role. `reverse_loss` uses neither `require_patron` nor an equivalent
librarian-only dependency; any valid key clears it. The PR description
says "a librarian may reverse the loss", not "a patron". A patron who
reported their own loss can immediately reverse it, get the copy back, and
never pay the replacement fee, with no librarian ever involved.

**The reversal window test (Major).** `REVERSAL_WINDOW_DAYS = 21` is a
threshold the feature introduces. The one shipped test that reverses a
loss does it in the same request that reported it, at day zero. Nothing in
the suite reverses on day 21 (still allowed) or day 22 (refused). A
reviewer who has seen a `<` become a `<=` by accident checks the number
next to the boundary, not the same day the loan was reported.

## 4. The clean trap

`reverse_loss` ends with `item.copies += 1` and calls nothing that
persists it. No `session.commit()`, no `session.flush()` beyond what the
repositories already do. Right after finding the premature commit in
`report_lost`, the instinct is to check every method in the file for the
same mistake in the opposite direction, missing a commit instead of adding
one it shouldn't have. That instinct is wrong here: convention 4 gives the
transaction boundary to `get_db`, which commits when the request succeeds.
Flagging this as "the copy restore is never saved, add a commit" is a
false positive, and the commit it asks for is the same class of mistake
just fixed in `report_lost`.

## 5. Design and tests

Two structural findings and one test finding round out the review, none of
them blocking.

- **The stringly-typed status check** (`service.py`, `reverse_loss`):
  `if loan.status != "lost":` compares against a raw string where
  `LoanStatus.LOST.value` is one import away and is what every other
  status comparison in this file already uses. No behavior difference
  today; the risk is a future rename of the enum's value breaking exactly
  this one line silently.
- **`can_reverse_loss` with no direct test**: a new public function in
  `domain/lending.py`, exercised only indirectly through `reverse_loss`'s
  tests. README convention 7 ("every public function has a test") is
  broken by omission, and it is exactly this function's boundary that the
  test finding above says the shipped suite never reaches.
- **The duplicated error-mapping** (`api.py`): `report_lost` and
  `reverse_loss` each repeat the same `except NotFound` / `except
  NotAllowed` block already copied three times above them by `create_loan`,
  `return_loan`, and `create_hold`. Worth a comment even though it works
  today, because the next loan endpoint copies it a sixth time.

Two questions about them:

1. `can_reverse_loss` and `replacement_fee_for` are both new, both pure,
   and both only one of them got a direct test in the shipped PR. What
   makes a pure function easy to forget to test directly, compared to one
   that lives inside a service method?
2. The duplicated error mapping has existed since the second endpoint was
   added, three PRs before this one. What made this the PR worth raising it
   in, and would you have raised it on the second endpoint instead if you
   had reviewed that one?

## 6. Questions worth asking the author

- What happens if `report-lost` is called twice on the same loan, by two
  different people, close together? (This is the question that finds the
  Blocker without reading a line of `service.py`.)
- The replacement fee is computed and returned, but never charged anywhere
  in this codebase, the same as the fine on a normal return. Is that
  intentional, and does a downstream billing system read it off the
  response?
- Should the reversal window be configurable per item or per patron (a
  rare book might get a shorter window), or is one global constant
  intentional for now?
- Who is allowed to see `lost_on` and the replacement fee, and does a
  patron's own loan listing expose either?
- The shipped tests cover a patron reporting their own loss, a librarian
  reporting on behalf of a patron, and one reversal. Which of the two most
  expensive failure modes, a repeated report or an unauthorized reversal,
  would they catch?

## 7. Five interviewer questions about the rewrite

1. The fix for the premature commit reorders three lines and deletes one.
   Argue for and against a larger alternative: wrapping the whole method in
   its own nested transaction (`session.begin_nested()`) instead. What does
   each approach cost the next reader?
2. `replacement_fee_for`'s fix caps the sum instead of one term. Is there a
   reasonable product argument for capping the replacement cost and the
   fine separately instead, and if so, what would the function's signature
   need to look like to express that?
3. The rewrite adds `require_librarian`, mirroring `require_patron`
   exactly. At what point would you introduce a shared `require_role(role)`
   helper instead of two near-identical functions, and what would you want
   to see in the codebase before making that call?
4. Every commit in the rewrite is small enough to revert on its own. Which
   of the seven would you be least comfortable shipping without a hidden
   test behind it, and why that one over the others?
5. None of the seven fixes added a new class or a new abstraction. Pick the
   one where a slightly more general version would have been tempting and
   explain what it would have cost the next reader who has to understand
   this feature in isolation.
