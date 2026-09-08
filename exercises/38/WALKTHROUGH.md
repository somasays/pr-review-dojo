# Exercise 38 walkthrough: parcel redirection

Mode: teach. Domain: sandbox (lockers). Difficulty: medium.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/89
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/91

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

This is the first exercise built on `sandbox/lockers/`, not `app/`, so the
first thing to read is not code at all.

1. **Read `sandbox/lockers/README.md` before anything else.** Its
   conventions are deliberately the opposite of `app/` and `sandbox/rooms/`
   in three places that matter for this PR: every datetime is naive UTC, not
   aware; repositories never commit, the service owns the transaction
   through `unit_of_work()`; and pickup codes are unique per locker among
   active parcels, generated with `secrets`. A reviewer who skips this file
   will get several of this PR's findings backwards.
2. **Open `sandbox/lockers/api.py` and find the new endpoint,
   `redirect_parcel`.** It is public, like pickup, not courier-key
   protected like deposit. That single fact is the first question worth
   asking: what proves the caller is the recipient?
3. **Follow the call into `RedirectService.redirect` in
   `sandbox/lockers/service.py`.** Read it next to `PickupService.pickup`
   and `DepositService.deposit` above it. `redirect` is doing a bit of
   both: it looks up an existing parcel like pickup, and it allocates a
   compartment and a code like deposit. Any place it borrows from `deposit`
   instead of preserving what pickup would expect is worth a second look.
4. **Then `sandbox/lockers/repo.py`,** specifically the new
   `CompartmentRepo.release` method `redirect` calls partway through.
5. **Then `sandbox/lockers/domain/fit.py`,** the new `can_redirect`
   function, which is one line and easy to skim past.
6. **Then the tests,** `test_service.py` and `test_api.py`. By now you know
   what the service does, so you are checking whether the tests exercise
   the parts that are easy to get wrong: the boundary of the redirect
   limit, and the failure paths.

## 2. What to grep for before commenting

- `commit(` in `sandbox/lockers/`. The service and repository files should
  have exactly the ones already in `unit_of_work` and nowhere else; a hit
  in `repo.py` is the tell for the transaction-scope finding.
- `parcels.get(` versus `parcels.by_code(`. `pickup` uses `by_code`, proof
  of possession. `redirect` uses `get`, an id lookup with no proof at all.
  The `code` field sitting in the request body and never checked anywhere
  is the confirming detail.
- `locker_id` versus `target_locker_id` inside `redirect`. Two locker ids
  are in scope for the whole method; every place one is used, ask which one
  it should be.
- `deposited_at` and `expires_at` inside `redirect`. `late_fee_cents` keys
  off `deposited_at`; anything that reassigns it after the parcel already
  has a value is worth stopping on.
- `Notifier` in `sweeper.py` versus the redirect notification. One is a
  callable injected by the caller, the other is a hardcoded function call.
- README conventions 3, 4, and 8. This PR touches all three.

## 3. The reasoning chain that surfaces each finding

**The repository commit (Blocker).** Grep `commit(` outside `db.py` and it
turns up once, in `CompartmentRepo.release`. That already breaks convention
4 on its own, but the interesting question is what it does to `redirect`:
this method opens one `unit_of_work`, so anything inside it should be
all-or-nothing. Trace what happens if `free_by_size` on the target locker
comes back empty right after `release` runs: the exception unwinds through
`unit_of_work`'s rollback, but `release`'s own commit already landed. The
old compartment is durably freed while the rest of the move never happens.
Nothing crashes, nothing logs an error, the recipient just gets a 409 and
the locker has quietly lost a compartment's worth of bookkeeping.

**The missing code check (Blocker).** `redirect_parcel` in `api.py` accepts
a `code` field in `RedirectRequest`. Ask what happens to it: it travels
into `RedirectService.redirect` as a parameter and is never read. The
lookup is `parcels.get(parcel_id)`, a bare primary key fetch. Parcel ids
are small auto-incrementing integers per README convention 1, so this is,
in practice, a redirect-any-parcel endpoint: guess an id, get a 200, and the
response hands back the new pickup code. Compare with `PickupService.pickup`
two classes up, which uses `by_code` precisely so the code is the proof.

**The reset deposit time (Major).** Read `redirect`'s last few lines next
to `deposit`'s. Both set `parcel.deposited_at = now` and
`parcel.expires_at = expires_at(now, HOLD_HOURS)`; in `deposit` that is
correct, a parcel is being created for the first time. In `redirect` the
parcel already has both fields from its original deposit, and the feature
spec is explicit that the original deposit time survives a redirect. The
consequence lives in `PickupService.pickup`, several files away: it prices
the late fee from `parcel.deposited_at`, so a redirected parcel is charged
as though it just arrived, undercharging every late pickup that involved a
redirect.

**The wrong locker for uniqueness (Major).** `_generate_code` takes a
`locker_id` and checks `parcels.by_code(locker_id, code)` in a loop. Inside
`redirect`, two locker ids are alive at once: `locker_id`, the source, and
`target_locker_id`, where the parcel is headed. The call passes `locker_id`.
The new code is about to live in the target locker, so this checks
uniqueness in the wrong room; the code that comes back can already be
active on some other parcel in the target locker.

**The hardcoded notification (Major, design).** `sweeper.py`'s `sweep`
takes a `Notifier` callable so a test can observe what was sent without
scraping logs. `redirect` calls a module-level `_notify_redirect` function
directly, with no way for a caller to intercept it. It works, but a test
here can only assert on log output, and the pattern the rest of this
codebase already established for exactly this situation goes unused.

**The untested pure function (Minor, design).** `can_redirect` is public,
one line, and pure, the easiest kind of function in this codebase to test
directly. Grep for its name in `tests/` and it appears nowhere except
inside `service.py` itself, so it is only exercised indirectly.

**The loose boundary test (Major, test).** `test_redirect_limit_eventually_
blocks_further_redirects` redirects a parcel through five lockers in a loop
and only asserts that `TooManyRedirects` fires somewhere in there. Ask what
value of `MAX_REDIRECTS` would make this test fail: none between 1 and 4
would. The test proves a limit exists, not that it is the right one.

## 4. The clean trap

`can_redirect(now, deadline)` returns `now <= deadline`, comparing two
naive datetimes with no `tzinfo` check inside the function. Coming from
`app/` or `sandbox/rooms/`, where aware datetimes are the rule, that looks
like a missing safety check. It is not: README convention 3 makes every
datetime in this codebase naive UTC by contract, and the actual boundary
check already happened once, in `ensure_naive_utc(now)` at the top of
`RedirectService.redirect`, before `can_redirect` is ever reached.
`late_fee_cents` and `expires_at` rely on the exact same boundary and do
not re-check it either. Flagging this as unsafe is the false positive;
asking where the naive-UTC contract gets enforced is a fair question with a
one-line answer.

## 5. Design and tests

Two design findings and one test finding sit alongside the four defects,
worth flagging even though none of them block the PR on their own.

- **The hardcoded notifier**: `sweeper.py` already solved "how does a
  caller observe what this method sent" with a `Notifier` callable
  parameter. `RedirectService` reinvents the same problem without the
  pattern that already exists two files away, so a test of the
  notification behavior has to either scrape a log or monkeypatch a module
  function.
- **The untested `can_redirect`**: pure, one line, easy to miss precisely
  because it is small. The gap does not show up in `pytest` output; it
  shows up the day someone changes the boundary and no test complains.
- **The five-argument `redirect` signature**: not a defect, `deposit` and
  `pickup` are plain positional arguments too, so this is consistent with
  the codebase. Worth naming anyway, because it is the widest signature in
  the module and the first candidate if the style is ever revisited.
- **The loose redirect-limit test**: the code path is small enough that a
  precise test costs almost nothing, three lines of assertion versus the
  five-line loop the original test used, and it would have caught an
  off-by-one that the loose version could not.

Two questions worth sitting with:

1. `can_redirect` and the `now > parcel.expires_at` check in
   `PickupService.pickup` express the same idea, "is this parcel still
   within its window", with two different boundary directions written in
   two different places (`<=` versus `>` on flipped operands). They happen
   to agree. What would you need to see to be confident a future edit to
   either one could not quietly disagree with the other?
2. `RedirectService` and `PickupService` both open one `unit_of_work` per
   public method, per convention 4. `RedirectService.redirect` does
   noticeably more inside that one transaction than either sibling method.
   At what point does "everything in one transaction" stop being a
   guideline and start being a reason to split the method?

## 6. Questions worth asking the author

- What proves the caller is the recipient, not just someone who knows the
  parcel id? (This is the question that finds the auth Blocker without
  reading a line of the diff.)
- What happens if the target locker fills up between the courier depositing
  and the recipient trying to redirect? Walk through what the database
  looks like right after that 409.
- Why does a redirect need to reset when the parcel was deposited? If the
  answer is "it shouldn't", where else in the codebase would that same
  mistake be easy to make again?
- The redirect limit is a flat constant. Should it be configurable per
  locker or per site, and is that a decision this PR is making implicitly
  by hardcoding it?
- Two lockers are in play throughout `redirect`. Would naming them more
  distinctly than `locker_id` and `target_locker_id`, or passing a small
  typed pair, have made the wrong-locker bug harder to write in the first
  place?

## 7. Five interviewer questions about the rewrite

1. The fix for the commit-in-a-repository-method issue is to delete the
   method and reuse `set_occupied`. What would you have done differently
   if `release` had needed to do something `set_occupied` could not, such
   as also clearing `notified_at`?
2. The code-uniqueness fix changes one argument, `locker_id` to
   `target_locker_id`. What test would you write to make sure a future
   refactor cannot silently swap it back, beyond the hidden test that seeds
   a collision?
3. The rewrite adds a `Notifier` parameter with a default value so every
   existing caller keeps working. What is the tradeoff of a defaulted
   parameter like this versus making the caller always pass one explicitly?
4. Every commit in the rewrite is independently revertible. Which one would
   you feel safest reverting on its own if it turned out to be wrong in
   production, and which one would break the most if reverted alone?
5. None of the seven commits add a new class, a new module, or a new
   abstraction beyond the one `Notifier` type alias. Pick the fix where you
   were most tempted to build something bigger, and say what it would have
   cost the next person reading this service.
