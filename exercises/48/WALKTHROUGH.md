# Exercise 48 walkthrough: lock out pickup after repeated wrong codes

Teach mode, sandbox (lockers), medium.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/114
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/116

Read the exercise PR with its inline comments first, then the rewrite PR
hunk by hunk, then this file.

## Reading order

**1. `sandbox/lockers/README.md`, in full, before touching the diff.** Five
conventions matter for this feature specifically: ids are auto-incrementing
integers, money never appears here so ignore that one, all datetimes are
naive UTC by convention and `db.ensure_naive_utc` rejects aware ones at the
service boundary, the service owns the transaction through `unit_of_work()`
and repositories never commit, and pickup codes are six digits unique among
active parcels in a locker. Nothing else in the README changed, so this
thirty seconds either confirms the new code fits or hands you the one
convention it quietly breaks.

**2. The PR description, turned into boundary inputs.** The description
states five numbers and two triggers plainly: a locker locks out after
`LOCKERS_LOCKOUT_MAX_ATTEMPTS` wrong codes within
`LOCKERS_LOCKOUT_WINDOW_SECONDS`, a lockout lasts `LOCKERS_LOCKOUT_MINUTES`,
a correct pickup clears the counter, a courier can clear one early, and an
alert fires once per lockout with a bounded retry. Turn each into a question
you will carry into the code: what happens on the attempt that hits the cap
exactly, what happens to a wrong code arriving the instant a lockout
expires, does two threads' wrong codes for the same locker ever get
undercounted, does the alert fire twice for one lockout or zero times if the
notifier is down, and does clearing reset every piece of state or just the
lock. Then one line per README convention this feature could plausibly
break: the naive-datetime convention, because this is the first feature in
this codebase to build a datetime from a duration rather than read one off
a row; the transaction convention, because the alert is an external side
effect that has to be sequenced against `unit_of_work()` somehow; and the
"repositories never commit" convention, which nothing here touches directly
but is worth confirming stays true once you see the new module does not
add a repository.

**3. `sandbox/lockers/lockout.py`, top to bottom, once, without judging.**
This is a new module with no existing counterpart in this codebase to
compare it against (concurrency.md's own framing applies here: nothing
else in the lockers sandbox starts a thread, so there is no local
convention to lean on for any of it). Build the mental model as two lists
before you read a single method body.

Surface (what a caller can do):

- `record_failure(locker_id) -> bool`
- `status(locker_id) -> LockoutStatus | None`
- `clear(locker_id) -> None`
- `alert(locker_id) -> None`
- `start()` / `stop(timeout)`

State (what is shared across request threads and the background thread):

- `_attempts: dict[int, list[float]]`
- `_locked_until: dict[int, float]`
- `_last_seen: dict[int, float]`
- `_lock: threading.Lock`
- `_thread`, `_stopped`

Now the table that is most of the review before you have read a line of
logic:

| Method | Reads or writes | Holds `_lock` for the whole read-modify-write? |
| --- | --- | --- |
| `record_failure` | `_attempts`, `_last_seen`, `_locked_until` | yes, one `with self._lock:` block |
| `status` | `_locked_until` (read), plus a real-time computation after releasing the lock | only for the dict read |
| `clear` | `_attempts`, `_locked_until`, `_last_seen` | yes |
| `alert` | calls `status`, then the notifier, no shared state of its own | n/a |
| `_run` / `_sweep` | `_locked_until`, `_last_seen`, `_attempts` | yes, inside `_sweep` |

`record_failure` and `_sweep` are the two methods that mutate more than one
dict, and both hold the lock for the whole operation: that is correct, and
it is why the attempt count itself is not where this exercise's
concurrency defect lives. The defect is one level up, in who gets to build
this class's instance in the first place.

**4. The wiring: `sandbox/lockers/api.py`, `get_lockout_tracker` and
`get_pickup_service`.** Two questions for the getter, same as any lazily
built module-level object: who can call this concurrently, and does the
object it builds have a side effect a duplicate would leave behind. The
answer to the first is "any two request threads, including the first two
the process ever sees," because FastAPI runs `def` handlers on a
threadpool and this dependency has no import-time initialization to shield
it. The answer to the second is yes: `LockoutTracker.start()` spawns a
thread. Compare this getter against `get_session_factory` in
`sandbox/lockers/db.py`, which is `@lru_cache`'d and totally safe, then ask
what is different about the two objects being built, not about the two
functions building them.

**5. `sandbox/lockers/service.py`, `PickupService.pickup`.** This is where
the tracker gets consulted (a fast-fail check before anything touches the
database), updated (on a wrong code, inside the same unit of work that
looked the code up), and cleared (on a correct code, before the fee is
returned). Notice where `self._lockout.alert(...)` sits: after the `with
unit_of_work(...)` block has already exited. That placement is deliberate
and correct: the transaction for a wrong-code attempt never writes
anything, so there is nothing for the alert to be "inside" or "outside" of
in a data-integrity sense, but paging ops is a slow, retrying, external
call, and this codebase's convention (repositories never commit, the
service owns exactly one transaction per call) is a strong hint that
nothing slow belongs inside that block regardless. This is the "side
effect relative to a transaction" question this exercise is built to
raise; here the answer given is the right one, which is worth noticing on
its own.

**6. `sandbox/lockers/tests/test_lockout.py` last, and read it for what it
does not do.**

## What to grep for

- `grep -n "def " sandbox/lockers/lockout.py` paired with
  `grep -n "_lock\b" sandbox/lockers/lockout.py`. Every method that
  mutates more than one dict and appears in the first list should appear
  next to a `with self._lock:` in the body. All of them do; this is what
  tells you the concurrency defect is not in the counting arithmetic.
- `grep -rn "threading\." sandbox/lockers/` across the whole sandbox, not
  just the diff. Before this PR the answer is nothing except what
  `sweeper.py` and the tests already use for `Session` thread-safety
  comments; there is no prior art for a background thread in this
  codebase to have copied correctly or incorrectly.
- `grep -n "datetime.now\|datetime.utcnow" sandbox/lockers/*.py` across the
  whole package. Every existing file uses `datetime.utcnow()`. The new
  file is the only hit for `datetime.now(`, and it is the only one with a
  `UTC` argument. That single grep is most of finding LG-11.
- `grep -n "^_tracker\|^_[a-z_]* = " sandbox/lockers/api.py` for module
  level state. Compare `_tracker` against `_courier_keys`, which is a
  function, not cached state: there is genuinely nothing else in this file
  shaped like `_tracker` to compare it against, unlike the rate limiter
  exercise where an existing `_sender` singleton was right there to
  contrast with. Compare it instead against `get_session_factory` in
  `db.py`, one file over.
- `grep -n "notify\|Callable\[\[Parcel" sandbox/lockers/*.py` to find
  `sweeper.py`'s `Notifier = Callable[[Parcel], None]` and put it next to
  `lockout.py`'s `notifier: _LogAlertNotifier | None`. Same codebase, same
  kind of seam, two different shapes.

## The reasoning chain for each defect

**The lockout tracker singleton (`api.py`, `get_lockout_tracker`).** The
chain: who can call this concurrently, and does building the object have a
side effect. FastAPI dependencies on `def` handlers run on the threadpool,
so the answer to the first is "yes, including the very first two requests
a cold process sees." The answer to the second is yes: `LockoutTracker`'s
constructor is cheap, but `start()` spawns a named thread. Two threads that
both observe `_tracker is None` both build a tracker and both start one;
whichever gets published last wins the module global, and the other is
orphaned with its sweep thread still running, unreachable by any future
`stop()` call, and its `_attempts` map invisible to the tracker every later
request actually uses. Severity: Blocker, because the failure mode is not
"slightly wasteful," it is "the brute-force protection this PR exists to
add can silently only see half of an attacker's guesses," which is the
security property failing under exactly the load it was built to survive.

**The swallowed alert (`lockout.py`, `alert`).** The retry loop is
correctly bounded and correctly narrow (it only catches
`AlertTransientError`, not `Exception`), which is what makes the ending
easy to miss: after three attempts fail, the method logs at error and
returns, with nothing distinguishing this from a successful `notify()` call
to any caller. Ask what happens if the paging integration itself is down
during an actual attack: the retry burns through its three attempts,
correctly, and then the method that exists to make sure ops finds out
returns as if it worked. Severity: Blocker, for the same reason as the
singleton: this is the exact failure the feature exists to prevent (nobody
finding out about a lockout) happening silently, under a condition (the
alert channel being unreliable) that a retry loop's presence already
admits is expected to happen sometimes.

**The naive-versus-aware slip (`lockout.py`, `status`, line with
`datetime.now(UTC)`).** This is the finding a reviewer who has not read
the README gets backwards. `datetime.now(UTC)` is the textbook-correct way
to build a timezone-aware UTC datetime, and in `sandbox/rooms` (a sibling
codebase in this same repository) it would be exactly right, because that
codebase requires aware UTC everywhere. Here it is exactly backwards:
`db.ensure_naive_utc` exists specifically to reject aware datetimes at the
service boundary, and `api.py`'s own module docstring promises "naive
datetimes serialize as plain ISO strings with no offset." Grep confirms it:
every other datetime built anywhere in this package uses `datetime.utcnow()`.
The `locked_until` field in the pickup response is the one field in this
API that comes back with a `+00:00` suffix. Severity: Major, not Blocker:
nothing crashes, because this codebase does not compare this particular
aware value against a naive one anywhere, but any client or downstream job
written against the stated "every timestamp here is naive" contract will
misparse or mis-sort this one field.

**The sleeping sweep thread (`lockout.py`, `_run`).** `while not
self._stopped: time.sleep(self._policy.sweep_seconds); self._sweep()`. The
question is not "does this eventually work," it does; it is "what can
interrupt this line while it is sleeping," and the answer is nothing.
`stop()` sets `self._stopped = True`, but the thread only reads that flag
after the sleep call returns. With the default 30 second interval, that
is the size of the window in which shutdown hangs, a test of shutdown has
to wait, and an expired lockout or an idle counter that should have been
pruned keeps sitting there. Severity: Major: this is a real cost paid on
every shutdown and every test of the sweep, but it degrades cleanup and
latency, not correctness of the counting or the lockout decision itself.

**The trap: `load_lockout_policy` under `@lru_cache(maxsize=1)`
(`lockout.py`, near the top).** This sits a few lines above the genuinely
unsafe singleton in `api.py`, which is most of why it is worth planting.
Both are "build something once and share it across request threads with
no explicit lock." The question that separates them is the same one from
the singleton finding: what happens to the loser of a cold-start race?
Here, the loser's `LockoutPolicy` is a frozen dataclass built from nothing
but `os.environ`, with no side effect; it is discarded and every caller
ends up holding an equivalent value. `get_lockout_tracker`'s loser has
already started a thread that nothing will ever stop. Pure value,
discard the loser: fine. Side effect, discard the loser: Blocker. Say
that distinction in the review and you have shown you understand the
actual hazard rather than pattern-matching on "shared mutable module
state."

**What the visible test tells you.** `test_lockout.py` has three solid,
single-threaded tests and one test that starts the real sweep thread with
a 0.05 second interval and then does `time.sleep(0.2)` hoping the sweep
already ran. It is the only test in the file that touches the background
thread at all, and it is the one written in the style the rest of this
codebase does not use, since nothing else here starts a thread. Say it as
a testing gap, not an accusation: the fix an interviewer wants to hear is
an injected clock and a direct call to the sweep step, not a longer sleep.

## Design and tests

**The notifier depends on a concrete class where this codebase already has
the right pattern one file away (DS-04).** `LockoutTracker.__init__` types
`notifier` as `_LogAlertNotifier | None` and builds that class when none is
given. `sweeper.py`'s `Notifier = Callable[[Parcel], None]` is the answer
already sitting in this package: a notifier is one function call, so it
needs nothing more than a callable type. A reviewer notices this by asking,
of any new class that takes a collaborator, "does this codebase already
have a seam shaped like this one two lines away." Here it does.

**`seconds_remaining` reads the real clock instead of taking `now`
(DS-09).** The signature is `seconds_remaining(deadline: float) -> int`,
which reads as pure: one float in, one int out. The body calls
`time.monotonic()` directly, even though `LockoutTracker` already carries
an injectable `self._clock` for exactly this reason. A reviewer catches
this the same way as any "looks pure but is not" function: check whether
every value the body uses actually arrived as a parameter. The fix takes
`now` as a required second argument, the way `record_failure` and `_sweep`
already do, and the one call site in `status` passes `self._clock()`.

**The refactor: retry-after text formatting mixed into the API handler
(DS-21).** Not a defect: the three-branch minutes-and-seconds string in
`pickup_parcel`'s `LockedOut` handler is short and correct today. A
reviewer notices it by asking, of any exception handler doing more than
one thing, which part of it could be tested without a request. Here the
answer is all of it: the formatting needs nothing but an integer.
Worth a comment phrased as an opportunity, since this handler will only
grow more branches (a different unit, pluralization, localization) the
next time someone touches it.

Two questions for the author, before you write the review:

1. The tracker's internal clock is `time.monotonic`, chosen so a lockout's
   duration cannot be stretched or shortened by an NTP adjustment mid-way
   through. Is that reasoning documented anywhere a future maintainer who
   only reads the naive-UTC convention in the README would find it, or
   does it live only in this PR's description?
2. `record_failure` prunes attempts older than the window on every call,
   and `_sweep` separately prunes idle counters on an interval. Is the
   per-call pruning in `record_failure` doing anything `_sweep` would not
   eventually do on its own, or is it there so a locker that is hit once
   and never again does not wait for the next sweep to have an accurate
   count?

## Five questions an interviewer would ask about the rewrite

1. The fix wraps the whole build-and-start of the tracker in one lock, so
   every request thread that loses the race blocks briefly on the winner's
   `LockoutTracker.__init__` and `start()`. At what point would that block
   become a real latency problem, and what would you change first: the
   lock granularity, or moving construction out of the request path
   entirely?
2. `stop()` calls `self._stop_event.set()` and then joins the thread with a
   default two second timeout. Walk through what happens if `_sweep()`
   itself is mid-flight, holding `self._lock`, when `stop()` runs from
   another thread. Does anything in this design ever deadlock, and if not,
   why not?
3. The rewrite fixes the swallowed alert by raising `AlertDeliveryFailed`
   out of `alert()` and catching it once, in `service.py`, purely to log
   it. What would you have to add to this codebase before "log it" stops
   being good enough, and where would that live?
4. `record_failure`'s per-attempt list, `_sweep`'s expiry pass, and
   `status`'s remaining-seconds computation all touch the same conceptual
   piece of state through three different code paths. If a second feature
   needed to read "how many wrong attempts has this locker had recently"
   for a dashboard, would you add a new method to `LockoutTracker`, or is
   that a sign the class is already doing two jobs?
5. This whole tracker is a single in-process object, which the PR
   description flags as a known limit if pickup ever runs behind more than
   one worker. If you had to move it to a shared store like Redis tomorrow,
   which of the eight things this rewrite fixed would still need fixing in
   the new implementation, and which were specific to it being in-process
   memory?
