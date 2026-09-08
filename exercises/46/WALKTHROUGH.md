# Exercise 46 walkthrough: partial line approval on expense claims

Mode: teach. Domain: sandbox (expenses). Difficulty: medium.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/109
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/111

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

This is built on `sandbox/expenses/`, not `app/` and not any of the other
sandboxes, so the first thing to read is not code at all.

1. **Read `sandbox/expenses/README.md` before anything else.** Its
   vocabulary section is what the PR description's rules turn into: the
   payable total, the month-cap re-check, the all-rejected boundary, and
   the self-approval rule are all named there, and its conventions are
   deliberately different from `app/`, `sandbox/rooms/`,
   `sandbox/lockers/`, and `sandbox/library/` in ways this PR could get
   backwards. Turn the PR description's own rules into the boundary inputs
   you will trace with, before opening a single changed file:
   - "the claim's payable total becomes the sum of the approved lines
     only" -> a claim with one approved line and one rejected line; does
     the total match the approved line alone, and does it stay in dollars
     rather than drifting to some other scale?
   - "the month cap is re-checked against the approved lines rather than
     the submitted ones" -> a claim submitted right at its category's
     monthly cap; does approving it at decision time re-trigger the cap
     check against a number that already includes this same claim once?
   - "a claim with every line rejected becomes rejected, otherwise
     approved" -> a claim with two lines, one rejected; does the claim
     land on approved, or does a single rejection flip the whole claim?
   - "the decision is idempotent for the same approver and claim" -> call
     `decide` twice with the same approver on an already-decided claim;
     does the second call return the same claim or raise?
   - "the employee sees per-line outcomes in the claim response" -> does
     the claim response actually carry outcome and reason per line, not
     just the claim's own status?
   - "the payout batch pays the payable total, not the submitted total" ->
     a partially-rejected, approved claim reaching the payout batch; does
     the batch total reflect only the approved lines?

   One line per README convention this feature could break:
   - Convention 1 (UUID4 string ids): nothing new gets an id here, low
     risk, but check any new row a rewrite might add.
   - Convention 2 (Decimal, quantized to cents, explicit currency): the
     highest-risk convention in this PR, since payable totals are new
     arithmetic on money.
   - Convention 3 (aware UTC datetimes): unaffected, `decided_at` already
     existed.
   - Convention 4 (the service owns the transaction, repositories flush
     but never commit): watch any new repository method this PR adds.
   - Convention 5 (claim status is an enum through one `transition`
     function): watch whether the new line-level outcome gets the same
     discipline, or reverts to bare strings.
   - Convention 6 (idempotency key, unique per employee): not touched by
     decisions, this is a submission-time convention.
   - Convention 9 (an approver may never decide a claim they filed
     themselves): the rule most likely to have a hole, since the PR adds
     a second way to call `decide`.

2. **Open `sandbox/expenses/api.py` and read `DecisionIn` and
   `decide_claim`.** This is the entry point: what a client can send, and
   how thin the mapping to the service is. Confirm the endpoint is still
   gated by `ApproverEmail`, not just `Identity`.
3. **Follow the call into `ClaimService.decide` in
   `sandbox/expenses/service.py`.** This is where almost everything in
   this exercise lives. Read it top to bottom once for shape before
   looking for anything wrong: the self-approval check, the branch for a
   whole-claim decision versus a per-line decision, the month-cap
   re-check, and the final status and payable total.
4. **Then the leaves this method calls into, before coming back to the
   composer:** `sandbox/expenses/domain/policy.py`'s `payable_total` and
   `claim_outcome` (new, pure, no IO, the easiest place to verify
   arithmetic and boundaries in isolation), then
   `sandbox/expenses/repo.py`'s `ClaimRepo.month_total_for` (existing) and
   whatever new repository method the month-cap re-check actually calls.
5. **Then `sandbox/expenses/service.py`'s `PayoutService.create_batch`,**
   to see what the batch total is actually built from now.
6. **Then the tests,** `test_service.py` and `test_api.py`. By now you
   know what the feature does, so you are checking whether the shipped
   test exercises the parts that are easy to get wrong: the boundary, the
   self-approval path, idempotent re-decision, and a tight month cap.

## 2. What to grep for before commenting

- `"approved"` and `"rejected"` as string literals in `service.py`. Every
  hit is a place `Claim.status`'s own convention, one enum, one transition
  function, was not extended to line outcomes.
- `owner.email == approver_email` in `service.py`. There is exactly one
  self-approval check; the question is whether it runs on every path
  through `decide`, or only some of them.
- `month_total_for(` in `service.py`. If the month-cap re-check reuses the
  submission-time query unchanged, ask what status the claim being decided
  is still in when that query runs.
- `* 100` and `/ 100` anywhere money crosses a boundary in
  `domain/policy.py`. A scale factor that goes on without a matching
  factor coming back off is the single most expensive kind of typo in this
  file.
- `approved_line_count` and `total_line_count` (or whatever the new
  boundary function's parameters are named) in `domain/policy.py`. Compare
  the comparison operator against the docstring's own words, "rejected
  only when every line was rejected."
- `.commit()` outside `db.py`. Should be zero hits; a hit in `repo.py`
  would be the tell for a transaction-scope finding, and this exercise
  does not have one, so use this grep to build confidence rather than to
  find something.
- `rejected_lines` in `service.py` and `api.py`. What shape travels
  through this parameter, and does every caller agree on it.
- `payable_total` in `tests/test_service.py` and `test_api.py`. If nothing
  in the shipped test ever reads this field, or the claim's `status`,
  after a partial decision, that gap is worth its own comment.

## 3. The reasoning chain that surfaces each finding

**The payable total scale mistake (Blocker).** Read the boundary input
from the PR description: does the payable total for one approved line of
20.00 come out as 20.00? Trace `payable_total` in `domain/policy.py`. It
multiplies every approved amount by 100 before summing, the standard first
step for converting to integer cents, and then returns that sum directly,
with no division back down. The consequence lives two calls away:
`PayoutService.create_batch` sums `claim.payable_total` straight into the
batch total it hands to whatever pays it out. Nothing here raises, nothing
here looks wrong in isolation, a claim payable of 25.00 just quietly
becomes 2500.00 in the one number a finance system reads to move money.

**The self-approval bypass (Blocker).** Read the boundary input: a claim
filed by the same person who is about to decide it, decided through the
per-line path rather than the whole-claim path. Trace `decide`. The
self-approval check exists, once, but it sits inside the branch for
deciding a claim as a whole. Feed it a non-empty `rejected_lines` and the
branch that holds the check never runs. The tell here is structural, not
arithmetic: a rule named explicitly in the README ("an approver may never
approve or reject a claim filed by that same approver's own email") is
enforced on only one of two paths into the same method.

**The month-cap double count (Major).** Read the boundary input: a claim
submitted for exactly a category's monthly cap, which submission itself
allowed, then approved at decision time with no other claims in play that
month. Trace the re-check. It calls the same query submission uses,
scoped to claims in `submitted` or `approved` status. The claim being
decided is still `submitted` at the moment this query runs, its status
does not change until several lines later, so its own lines are already
inside the "existing" total the query returns, before the newly approved
amount from this same claim is added on top in the caller. The claim gets
checked against itself twice and refused for a cap it never actually
crossed.

**The all-rejected boundary (Major).** Read the boundary input directly
from the PR description: two approved lines, one rejected line, does the
claim land on approved? Trace the new boundary function in
`domain/policy.py`. Its docstring says rejected only when every line was
rejected. Its comparison says rejected whenever the approved count is less
than the total count, true the moment even one line is rejected. Since
only approved claims are ever picked up by the payout batch, the two
lines the approver meant to approve are silently never paid, with nothing
in the claim's own state pointing at why.

**The raw tuple parameter (Minor, design).** `decide`'s new parameter is
a bag of two-element tuples. Every call site has to remember, without help
from the type checker, which position is the line id and which is the
reason.

**The bare outcome strings (Minor, design).** Line outcomes are assigned
and compared as `"approved"` and `"rejected"` literals in five places
across the two branches of `decide`, while `Claim.status` a few lines away
never does this without going through `ClaimStatus` and `transition`.

**The redundant approve flag (Minor, refactor).** `approve: bool` and
`rejected_lines` can now disagree, and nothing rejects the combination
`approve=False` with a non-empty `rejected_lines`; the per-line intent is
simply dropped. Not blocking today because the one caller never sends
both, but the claim's own outcome could be derived from `rejected_lines`
alone once it exists, retiring the flag.

**The shipped test (Major).** It rejects one line, approves another, and
asserts on the two lines' `outcome` fields only. It never reads
`decided.status` or `decided.payable_total`, the exact two fields the
all-rejected boundary and the scale mistake break. A single added
assertion on `status` would have caught the boundary problem outright.

## 4. The clean trap

`ClaimRepo.save` in `repo.py` only calls `self.session.flush()`, never
`.commit()`. That looks like a missing commit and it is not:
`db.unit_of_work()` is the only place a transaction commits in this
codebase (README.md convention 4), and the flush here exists so the
caller's later reads in the same transaction see the just-mutated line
outcomes and claim status before `unit_of_work` closes. Asserting "this
never persists, add a commit" is a false positive, and the commit it
would ask for is itself the correct-elsewhere, wrong-here move: a
repository that commits on its own is exactly how `sandbox/rooms/repo.py`
works, and exactly what would break the transaction boundary here.

## 5. Design and tests

Beyond the four defects, three findings are worth a comment even though
none of them block merge, plus the shipped test's gap covered above.

- **The raw tuple parameter.** `rejected_lines: Sequence[tuple[str, str]]`
  reads fine at the definition and ambiguous at every call site. A small
  frozen dataclass with named fields costs four lines and turns a swapped
  argument order into a type error.
- **The bare outcome strings.** Five string literal comparisons for a
  two-valued concept that already has a sibling, `ClaimStatus`, showing
  exactly what the enum-plus-one-transition-function pattern looks like
  three lines above.
- **The redundant `approve` flag.** Worth flagging as a "the next caller
  gets to discover this the hard way" issue rather than a blocking one:
  today there is one caller and it never sends conflicting values.

## 6. Questions worth asking the author

- What happens if this endpoint is called twice with the same rejected
  lines? (This is the question that finds the intended idempotent
  behavior, and also the question a reviewer should ask about every write
  endpoint in this codebase.)
- If an approver rejects every line on a claim, does the employee get a
  clear signal why, or just a claim that quietly never gets paid?
- Is a rejected line's reason ever shown back to the approver on a later
  decision, or only to the employee? The response schema carries it either
  way; who is expected to read it?
- Does the monthly cap re-check need to run at all when the approver
  rejects every line? Today it still runs and finds nothing to check
  since there are no approved amounts, which is harmless, but worth
  confirming that was deliberate rather than accidental.
- The PR adds a `payable_total` column to `Claim`. What happens to a claim
  that was approved before this migration and never got one set?

## 7. Five interviewer questions about the rewrite

1. The self-approval check moves from inside one branch to above both of
   them, a three-line change. Why does this fix have to move the check
   rather than duplicate it into the second branch, and what would
   duplicating it cost the next person who edits `decide`?
2. The month-cap fix adds a whole new repository method instead of
   patching the existing query in place. What would patching
   `month_total_for` itself, rather than adding
   `approved_month_total_for`, have broken for the submission-time cap
   check that still needs the old behavior?
3. `payable_total`'s fix removes a scale-then-quantize step and replaces
   it with sum-then-quantize. Walk through why the two are not equivalent
   for a claim whose lines do not already round to whole cents, and why
   they happen to be equivalent here.
4. The rewrite introduces `LineOutcome` and `LineRejection` as two small,
   separate types rather than one combined type carrying a line id, an
   outcome, and an optional reason. What would combining them have cost at
   the one call site in `api.py` that builds a list of rejections but
   never needs to express "approved" as a value?
5. Every commit in the rewrite is small enough to revert on its own. If
   you could only ship two of the six before a release deadline, which two
   would you pick, and what does shipping without the other four cost in
   the meantime?
