# Exercise 40 walkthrough: loan renewals

Mode: teach. Domain: sandbox (library). Difficulty: medium.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/94
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/96

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

This is the first feature you will review on `sandbox/library/`, so before
opening a single line of the diff, read `sandbox/library/README.md` in
full. Its conventions are deliberately different from `app/`,
`sandbox/rooms/`, and `sandbox/lockers/`, and this PR is built to break a
few of them in ways that would be correct code in those other sandboxes.
The convention that matters most here is number 4: repositories never
commit, and only the API's `get_db` dependency and the overdue job call
`session_scope()`. Everything else in this PR is easier to read once that
one sentence is loaded.

1. **`sandbox/library/domain/lending.py` first, specifically `can_renew`
   and `due_date`.** Both are untouched by this PR, both already have
   tests, and both are exactly the functions a renewal feature should
   reach for. Knowing what already exists is what lets you notice, later,
   when the service reimplements a piece of it by hand instead of calling
   it.
2. **`sandbox/library/api.py`, the new `POST /loans/{loan_id}/renew`
   endpoint.** This is the entry point; read it for what it trusts and
   where that trust comes from, the same way you would read any other
   auth-adjacent code first.
3. **`sandbox/library/service.py`, `renew_loan`.** This is the call tree's
   next layer and where most of the logic lives. Read it end to end once
   for the happy path, then a second time asking, for every line that
   changes something, "what happens if the very next check after this
   line fails?"
4. **`sandbox/library/repo.py`, `HoldRepo.other_patron_holds`.** A leaf:
   one query, no logic above it. Leaves are cheap to verify in isolation,
   so check its `WHERE` clause against the two existing hold queries a few
   lines above it before you trust it.
5. **`sandbox/library/overdue.py`.** Not touched by any defect, but read
   it anyway: it is the second of the two callers of `session_scope()`,
   and the PR changes what it reports for a renewed loan. Confirming this
   part is correct is part of confirming the feature is done, not just
   that the new endpoint works.
6. **`sandbox/library/tests/test_renewals.py` last.** By now you know what
   the feature does, so you are only checking whether the tests would
   catch it if it broke. They will not, in one specific way.

## 2. What to grep for before commenting

- `MAX_RENEWALS` across `sandbox/library/`. One definition in
  `service.py`, one read inside `renew_loan`, one read inside
  `can_renew`'s test. Notice which of the two reads the new code actually
  uses.
- `fulfilled_at` in `repo.py`. Three hold queries; count how many filter
  it out.
- `commit(` in `sandbox/library/service.py`. One hit is one hit too many,
  the same signal it would be in `app/services/`, but here the fix is not
  "add a commit", it is "there should not be one at all".
- `== "` in `sandbox/library/service.py`, to catch a status compared
  against a raw string next to lines that compare against the enum.
- `session_scope` in `sandbox/library/`. Exactly two call sites,
  `api.py` and `overdue.py`, per the module docstrings. A third would be
  the tell for a defect that is not in this PR; here it confirms the
  convention rather than breaking it.

## 3. The reasoning chain that surfaces each finding

**The on-behalf-of bypass (Blocker).** Read `renew_loan` in `api.py` next
to `list_patron_loans`, which already has the librarian-or-self pattern
this codebase uses for exactly this situation: `role != "librarian" and
patron.email != email`. The renew endpoint does not have that shape. It
takes `body.on_behalf_of_patron_email or email` unconditionally, so the
question "who is `role` for?" answers itself: it is captured and never
read. A parameter a function accepts but never uses is worth a second
look on its own; here it is the whole bug.

**The commit in the wrong place (Blocker).** Ask the idempotency and
transaction-boundary question convention 4 forces on every write in this
package: who commits this, and when? Trace a renewal on an item another
patron holds. The loan's `due_on`, `frozen_fine`, and `renewals` are all
mutated, then `self.session.commit()` runs, and only after that does the
hold check raise. The 403 the caller sees is correct, but the database
disagrees with it: the renewal already happened. The tell that this is
structural, not incidental, is the same one that surfaces double-restock
bugs elsewhere in this repository: a method that is supposed to be
all-or-nothing has a commit sitting between some of its "all" and the
rest of it.

**The renewal cap boundary (Major).** `can_renew` already exists, already
has a passing test for exactly `renewals_so_far == max_renewals`, and
`renew_loan` does not call it. Instead there is a fresh, inline
`loan.renewals > MAX_RENEWALS`. Whenever a PR introduces a check that
duplicates something the codebase already has, read the duplicate as
carefully as if it were new, because it usually is: `2 > 2` is `False`, so
a loan at exactly the cap renews once more before the fourth attempt
finally trips the check.

**The hold check with no expiry (Major).** `other_patron_holds` filters
`item_id` and `patron_id != patron_id`. Compare it to `first_for_item` and
`active_for_item`, two methods above it, both of which add
`Hold.fulfilled_at.is_(None)`. The new method is the odd one out in its
own class. Any hold ever placed by a different patron, fulfilled or not,
blocks every future renewal of that item for whoever holds it now.

**The untested boundary (Major).** Count the renewals in every test in
`test_renewals.py`: every one of them renews a loan exactly once.
`MAX_RENEWALS = 2` is a threshold this PR introduces, and nothing in the
shipped suite goes near it, which is exactly why the boundary defect above
ships behind a fully green run.

**The string literal (Minor).** `if loan.status == "lost":` sits directly
above `if loan.status == LoanStatus.RETURNED.value:`. When two adjacent
lines do the same kind of comparison and only one of them uses the
vocabulary the codebase defines for it, the other one is worth a comment
even though today it happens to compare equal.

**The untested repository method (Minor).** `other_patron_holds` is new,
public, and reached only through `renew_loan`'s tests. README convention
7 says every public function has a test; this one has none of its own,
so a bug in its predicate (see above) surfaces two layers away as a
confusing renewal failure instead of a direct assertion.

## 4. The clean trap

`loan.frozen_fine = loan.frozen_fine + fine_so_far` looks like it is
missing a `.quantize(Decimal("0.01"))` call before the value goes back
onto the row. It is not missing anything: `fine_for` already returns a
cent-quantized `Decimal` with `ROUND_HALF_UP`, and the `frozen_fine`
column defaults to a cent-quantized `Decimal`. Adding two cent-quantized
Decimals cannot introduce drift; there is no fractional cent anywhere in
this expression for a `.quantize()` call to clean up. Flagging this as
"money math needs explicit rounding" is applying a rule that already
holds without the extra call.

## 5. Design and tests

Two design findings and one refactor opportunity round out the review,
alongside the test gap above.

- **The untested repository method** (`repo.py`,
  `other_patron_holds`): covered above as a finding in its own right, and
  worth naming again here as the kind of gap a reviewer should expect
  from any PR that adds a repository method without touching
  `tests/test_repo.py` (which, before this PR, does not exist).
- **The string literal status check** (`service.py`, the `"lost"`
  comparison): a Minor finding, not a behavior bug today, but a trap for
  whoever renames `LoanStatus.LOST`'s value later and greps for the enum
  instead of the string.
- **Duplicated error mapping** (`api.py`, `create_loan`, `return_loan`,
  and now `renew_loan`): each handler repeats the same
  `except NotFound` / `except NotAllowed` block. Not blocking on its own,
  the same way it was not blocking the first time this codebase saw the
  pattern repeat, but the third occurrence is the one worth commenting on
  before a fourth endpoint copies it again.

Two interviewer-style questions about them:

1. The repository method gap and the string-literal gap are both Minor
   findings about code that behaves correctly today. If you could only
   land one fix before merge, which one, and what has to go wrong in
   production before the other one actually costs anyone anything?
2. `renew_loan` now has five guard clauses before it touches the loan.
   At what point, if any, does a chain of guard clauses like this start
   to argue for a small validation object or a policy function instead of
   five sequential `if` statements? Is this PR past that point?

## 6. Questions worth asking the author

- What happens if this endpoint is called twice in a row for the same
  loan with a competing hold in between? (This is the question that finds
  the commit-ordering Blocker without reading past the method's first
  branch.)
- `frozen_fine` is recorded on the loan but never surfaced anywhere a
  patron or librarian can see it except the raw API response. Is showing
  it somewhere a follow-up, or is recording it enough for now?
- Why does `renew_loan` re-check `patron.blocked` and loan ownership
  independently of the API's identity check, the same way `return_item`
  does? Is that redundancy deliberate defense in depth, or copy-paste
  from `return_item`?
- The overdue job now adds `frozen_fine` to what it reports. Does a loan
  that is renewed and then goes overdue again get notified about the
  frozen portion more than once, and should it?
- Should a librarian renewing on behalf of a patron leave any trace of
  who actually made the call, beyond the loan's own `renewals` counter?

## 7. Five interviewer questions about the rewrite

1. The fix for the on-behalf-of bypass adds one `if` around one field
   instead of removing the field entirely, unlike a similar defect fixed
   elsewhere in this repository by deleting the field outright. What
   makes this one different, and how would you tell, in general, whether
   a client-supplied override is a feature to gate or a mistake to
   delete?
2. The commit-ordering fix reorders one check and deletes one line, with
   no new abstraction. Argue for the larger alternative: wrapping
   `renew_loan`'s mutations in an explicit local transaction that rolls
   itself back on any later failure. What would that buy you here, and
   what would it cost the next method that has to reason about which
   transaction it is actually running inside?
3. The renewal-cap fix replaces an inline comparison with a call to an
   existing, previously unused function. What is the risk in reusing a
   function that was tested in isolation but never exercised through the
   code path that now calls it, and how would you retire that risk before
   merging?
4. Every commit in this rewrite changes one file. Which of the seven
   would you be least comfortable shipping on its own, without the others,
   and why?
5. None of the seven fixes introduced a new class or a new module. Pick
   the one where you were most tempted to add one, and say what it would
   have cost the next reader if you had.
