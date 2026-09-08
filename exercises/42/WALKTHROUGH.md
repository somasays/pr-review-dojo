# Exercise 42 walkthrough: booking amendment

Mode: teach. Domain: sandbox (rooms). Difficulty: medium.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/99
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/100

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

This PR touches `sandbox/rooms/`, not `app/`. Before opening a single
changed line, read `sandbox/rooms/README.md` in full. Its conventions are
deliberately different from `app/`'s, and this PR is built in a way that
breaks a couple of them in the specific spot where the two codebases would
otherwise look interchangeable. The two conventions that matter most here
are number 1 (money is integer cents, never `Decimal`) and number 2
(repositories commit; every method is its own unit of work, the opposite of
`app/db/repositories.py`, where the caller owns the transaction). Everything
else in this PR reads differently once those two sentences are loaded.

Then turn the feature description into boundary inputs before reading any
code. The PR description states four rules in plain language: the new slot
must not conflict with any other active booking in the room except the
booking's own current slot, the price is recomputed and the difference
reported, an amendment is refused within 30 minutes of the current start and
once it has started, and the reminder flag is reset. Each of those is a
one-line spec with an edge to test: what happens at exactly 30 minutes, what
happens when the "other" booking is actually a cancelled one, what happens
when the "conflict" query finds the booking's own row. Hold those questions
in mind; they are where the findings live.

1. **`sandbox/rooms/domain/slots.py`, `can_amend`, first.** It is a pure
   function, five lines, and it is the single place the 30 minute rule from
   the PR description is implemented. Reading it before anything else means
   you can evaluate the boundary in isolation, with no database and no
   ordering to reason about.
2. **`sandbox/rooms/api.py`, `amend_booking`, next.** This is the entry
   point. Read it for what it trusts, the same way you would read any
   auth-adjacent handler first: what does it receive from the authenticated
   caller, and what does it actually do with it?
3. **`sandbox/rooms/service.py`, `BookingService.amend`, then.** This is
   where the call tree's logic actually lives. Read it once for the happy
   path, then a second time asking, for every line that changes something,
   "what happens if the very next check after this line fails?"
4. **`sandbox/rooms/repo.py`, `find_conflicts_excluding`, `update_slot`, and
   `reset_reminder`, as leaves.** Each is one query or one write with no
   logic above it. Leaves are cheap to verify in isolation: check
   `find_conflicts_excluding`'s `WHERE` clause against `find_conflicts`, a
   few lines above it, which does the same job for a brand new booking.
5. **`sandbox/rooms/tests/test_service.py` last.** By now you know what the
   feature does, so you are only checking whether the shipped tests would
   catch it if it broke. They will not, in one specific way.

## 2. What to grep for before commenting

- `holder_email` in `api.py`. `cancel_booking` compares it against the
  booking; count how many of the three write endpoints do the same.
- `cancelled_at.is_(None)` in `repo.py`. Three query methods filter active
  bookings this way; check whether the newest one is a fourth.
- `commit(` in `repo.py`. Every hit here is expected and correct, per
  README convention 2; this is the opposite of what the same grep means in
  `app/db/repositories.py`.
- `Decimal` anywhere in `sandbox/rooms/`. Zero hits is the expected answer;
  this package prices everything in integer cents.
- `AMEND_CUTOFF` in `domain/slots.py` and in `tests/`. One definition, one
  read inside `can_amend`, and however many test cases touch it. Notice how
  close to that value the test cases actually land.

## 3. The reasoning chain that surfaces each finding

**The missing ownership check (Blocker).** `amend_booking` receives
`holder_email` from the authenticated API key, the same way every other
booking endpoint does, but it never reads that variable except to hand it
to the service. Read `cancel_booking` a few lines below it: it loads the
booking through the service and the service compares `booking.holder_email`
to the caller before doing anything else. `amend` has no such comparison.
A parameter that is received but never checked is worth a second look on
its own; here it is the whole issue. Trace what happens when someone who is
not the holder calls this endpoint with a booking id they found or guessed:
it succeeds, and the response hands back the real holder's room, price, and
email.

**The write before the check (Blocker).** Ask the question the README's
commit convention forces on every write in this package: what happens when
a step after this one fails? `amend` calls `update_slot`, which commits
immediately, and only after that does it call `find_conflicts_excluding`.
Trace an amendment onto a slot another booking already holds: the booking's
start, end, and price are overwritten and committed, the conflict check
then finds the other booking and raises, and the caller sees an error as if
nothing happened. It did happen. Because every repository method here
commits on its own, there is nothing left to roll back once `update_slot`
returns; the room now has two active bookings in the same slot. The tell is
structural: the cutoff check above it already runs before any write, so the
conflict check breaking that pattern is the one line worth stopping on.

**The cancelled-row conflict (Major).** `find_conflicts_excluding` excludes
the booking's own id, correctly, but drops the `cancelled_at.is_(None)`
filter that `find_conflicts` two methods above it has. Reading the two
methods side by side is what surfaces this; neither one alone looks wrong.
The consequence: any room that has ever had a cancelled booking in a given
slot can never have another booking amended into that slot again, even
though the room has been free the whole time.

**The cutoff boundary (Major).** `can_amend` returns
`current_start - now > AMEND_CUTOFF`. Evaluate it at the boundary the PR
description names explicitly, exactly 30 minutes: the expression is
`False`, so the amendment is refused a full minute before the rule the PR
describes. This is the same shape of off-by-one that shows up whenever a
threshold comparison is written by feel instead of checked against its own
edge; the fix is one character, `>` to `>=`.

**The test gap (Major).** Both amend-refusal tests in
`test_service.py` use values far from the cutoff: five minutes before start,
and already started. Neither one is anywhere near 30 minutes, so the
boundary issue above ships behind a fully green suite. A reviewer who reads
the PR description's stated threshold and then checks whether any test
touches it finds this gap without reading the implementation at all.

## 4. The clean-code trap

`BookingRepo.update_slot` ends with `self.session.commit()`. In `app/`, the
identical pattern is a real issue: repositories there never commit, because
the caller owns the transaction. In `sandbox/rooms/`, README convention 2
says the opposite: every repository method is its own unit of work and
commits before returning. A reviewer who has spent more time in `app/` than
in this package will recognize the shape and flag it on reflex; that
recognition is exactly what makes it a trap. Asking "does this package's
README say repositories commit?" costs one grep and settles the question.

## 5. Design and tests

Two more findings and one opportunity, none of them blocking, all worth
raising in the same review.

- **`can_amend` shipped with no direct test.** It is public and pure, and
  every other public function in `domain/slots.py` has a test that calls it
  directly; this one is only exercised indirectly through `amend`'s
  service-level tests. A direct test needs no database and would have
  caught the boundary issue above without touching a session at all.
- **`amend` takes raw datetimes instead of a `Slot`.** `book` already
  takes `slot: Slot`; `amend` takes `new_start: datetime, new_end: datetime`
  and builds the `Slot` itself. The caller in `api.py` already has
  everything it needs to build a `Slot` before calling either method, and
  building it in one place keeps the half-hour-alignment and
  maximum-duration validation in one call shape instead of two.
- **The exception-mapping refactor** (not blocking): `create_booking`,
  `amend_booking`, and `cancel_booking` each hand-map the same service
  exceptions to the same status codes. Worth pulling into one helper before
  a fourth booking endpoint copies the pattern a third time.

## 6. Questions worth asking the author

- What happens if this endpoint is called on a booking that is not yours?
  This is the question that finds the Blocker without reading a line of
  the service.
- What happens if the target slot is already taken? Specifically: does the
  original booking still hold its original slot after the refusal, or has
  it already moved?
- Is 30 minutes measured against the booking's original start or its
  current one, if it has already been amended once? The PR description says
  "current start," but nothing in the tests amends the same booking twice.
- Should there be a limit on how many times a single booking can be
  amended? Nothing in the model tracks a count.
- The reminder is reset on every successful amendment. What happens to a
  reminder that was already sent for the old start time if the amendment
  fails partway through?

## 7. Five interviewer questions about the rewrite

1. The fix for the write-before-check issue moves three lines. Argue for
   and against the larger alternative, wrapping the two repository calls in
   an explicit transaction object to restore atomicity. What does each
   approach buy you given that every repository method here already commits
   on its own?
2. The rewrite adds a direct test for `can_amend` and a test that pins the
   cutoff boundary as two separate commits. Why might a reviewer prefer that
   split over one commit that does both, and when would combining them be
   the better call instead?
3. `_as_http_error` is introduced in the last commit of the rewrite, after
   all four behavioral fixes. Would you have made this the first commit
   instead, and what would that change about how easy the rest of the
   rewrite is to review?
4. None of the eight commits in the rewrite add a new class, a new module,
   or a new abstraction beyond the one small helper function. Pick the
   finding where you were most tempted to add one, and say what it would
   have cost the next reader.
5. The ownership check and the write-ordering fix both touch
   `BookingService.amend` and land in separate commits. What would go wrong
   if they were combined into a single commit, and what would a reviewer
   lose by reviewing them together instead of separately?
