# Exercise 15 walkthrough: rate limit write endpoints per API key

Teach mode, concurrency, easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/32
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/39

Read the exercise PR with its inline comments first, then the rewrite PR
hunk by hunk, then this file.

## Reading order

**1. The PR description, then the settings.** The description says two things
that set up everything else: the counter lives in this process, and the
limiter is built lazily on the first request. Both are design choices, both
are defensible, and both are where the defects are. Note the author already
wrote down the one thing most people forget, that the cap has to move to a
shared store when there is more than one worker. That is a good sign about
the author and a bad sign about where to look for the mistakes: they are not
in the part that was thought about.

**2. `app/services/config.py` and the README rows.** Thirty seconds. Two new
integers with defaults, wired the same way as the seven settings already
there. Nothing to say. Do this first so you never have to come back to it.

**3. `app/services/rate_limiter.py`, top to bottom, once, without judging.**
Build the mental model: a dict of counts, a dict of window starts, a lock, a
thread. Notice which methods touch which dicts. Write it down if you have to:

| Method | Reads or writes | Holds |
| --- | --- | --- |
| `hit` | `_hits`, `_window_start` | nothing |
| `snapshot` | `_hits` | `self._lock` |
| `sweep` | `_hits`, `_window_start` | a lock |
| `_run` | calls `sweep` | nothing |

That table is the review. Two of the four rows touch both dicts and only one
of them holds the instance lock. You have not read a line of logic yet.

**4. `app/api/deps.py` from line 107 down.** Three new module level names and
two functions. Ask one question of each: who can call this at the same time
as someone else, and what does it mount on the process.

**5. `app/api/routers/orders.py`.** Confirms the dependency runs on four
endpoints, which means four handlers, which means the threadpool. This is the
line that turns everything above from "concurrent in principle" into
"concurrent on the second request".

**6. `tests/test_rate_limiter.py` last, and read it for what it does not do.**

## What to grep for

- `grep -n "def " app/services/rate_limiter.py` and pair it with
  `grep -n "_lock\|_hits\|_window_start"`. Any method that appears in the
  second list and not next to a `with self._lock` in the first is a
  candidate. This is the fastest way into a concurrency diff and it takes ten
  seconds.
- `grep -rn "threading\." app/` across the whole repo, not just the diff.
  Before this PR the answer is nothing: no thread, no lock, no event anywhere
  in `app/`. That matters. There is no local convention to copy, so every
  choice here was made from scratch, and the reviewer cannot lean on "it
  matches the rest of the codebase" for any of it.
- `grep -rn "^_[a-z]" app/api/deps.py` for module level state. You get
  `_sender` (pre-existing) and `_rate_limiter` (new). Compare them: one is
  built at import time, one during a request. That is the whole of CC-04.
- `grep -n "lru_cache" app/` to find every cached zero-argument function.
  There are now four. Three have been there for months.
- `grep -rn "def " app/api/routers/orders.py` to count how many handlers are
  `def` rather than `async def`. All of them. FastAPI runs those on a
  threadpool.

## The reasoning chain for each defect

**The counter increment (line 51).** Two steps, no lock, shared dict. The
argument the author made to themselves is written in the comment above it,
which is the gift this diff gives you: `# A dict item write is atomic, no
lock needed`. The claim is half true, which is why it is convincing. A single
`__setitem__` is atomic. `d[k] = d.get(k, 0) + 1` is a read, an add, and a
write, and the interpreter can switch threads between any two of them. Then
look up four lines: the window reset has the same shape and is easier to miss
because it is spread over an `if` and three statements rather than sitting on
one line. The severity argument: this is Major, not Blocker, because the
failure is undercounting, which lets more traffic through than intended. It
does not corrupt an order or leak a customer's data. It does mean the limiter
is least accurate exactly when it matters, which is why it is not Minor.

**The lazy singleton (`deps.py` line 125).** The chain is: who calls
`get_rate_limiter`, and from where? It is a FastAPI dependency on four `def`
handlers, so the answer is "any two request threads at once, including on the
very first two requests a fresh process ever sees". Then read the three lines
as a sequence rather than as a block: test, construct, start, assign. Nothing
stops both threads from passing the test. Two limiters is not just a wasted
object. They count into separate maps, so the effective cap doubles, and the
orphaned one's sweeper thread has no reference anywhere that could stop it.
This is the Blocker: the feature does not do the thing it was merged to do,
and the failure is invisible because both limiters behave perfectly on their
own. Contrast it with `_sender` twenty lines up, which is the same singleton
pattern and has never had this problem, because it is built at import time
under the interpreter's import lock. The author copied a safe pattern into a
place where the thing that made it safe no longer holds.

**`time.sleep` in `_run` (line 92).** Easy to spot, easy to under-argue.
The comment that lands is not "use an Event because Events are better", it is
"`stop()` currently does nothing for up to a window, so shutdown hangs and no
test can watch this loop without waiting a minute". The fix removes a piece
of state rather than adding one, which is the tell that it is the right fix.

**The trap: `@lru_cache` on `rate_limit_policy` (line 112).** This is placed
thirteen lines above a genuine unsynchronized lazy singleton, which is the
whole point. Both are "build something once and share it across request
threads with no lock". One is fine and one is a Blocker. The question that
separates them is not about the caching mechanism, it is: what happens to the
loser of the race? For `rate_limit_policy` the loser's `RateLimitPolicy` is
an equal frozen dataclass that gets garbage collected, and nobody can tell.
For `get_rate_limiter` the loser has already started a thread that nothing
will ever stop. Pure function, discard the value, no harm. Side effect,
discard the object, keep the side effect. Say that in the review and you have
demonstrated you understand thread safety rather than pattern matching on
`lru_cache`. Asserting the `lru_cache` is broken costs five points; asking
whether the policy should be re-read if settings change costs nothing.

**What the visible test tells you.** `tests/test_rate_limiter.py` is five
honest single threaded tests and one API test. Not one of them starts a
second thread. That is the strongest signal in the diff: the author tested
the arithmetic of a component whose entire reason to exist is that several
threads use it at once. Say it as a testing gap rather than as an accusation,
and say what a good test would look like, that is, a forced interleaving with
a barrier or an injected delay, not a sleep and a hope.

## Design and tests

The new `/reports/rate-limits` endpoint in `app/api/routers/reports.py` is
where the design and test findings live, and it rewards the same read-it-
twice habit as the concurrency defects: once for what it does, once for how
it is put together.

**`rate_limit_usage` does three jobs in one function (DS-21).** Read it as a
sequence: fetch the snapshot, work out the reset countdown, build the
percentage string. The first two need the limiter. The third needs nothing
but four plain values. A reviewer notices this by asking, of any function
over about ten lines, "which part of this could I test without a limiter,
without a request, without a session?" Here the answer is the last three
lines, and the fact that they are not already pulled out is the finding. The
fix is not a class or a formatter interface, it is one function that takes
`key`, `hits`, `limit`, and `resets_in_seconds` and returns the dict.

**`seconds_until_reset` reads its own clock (DS-09).** This one hides inside
a function that otherwise looks pure: two `int` and one `float` in, one `int`
out. The tell is the body, not the signature: `time.monotonic()` appears
where a parameter should be. A reviewer catches this by checking, for any
function with "pure" written all over its shape, whether every value it
uses actually arrived as an argument. The fix takes `now` as a required
parameter, the same way `hit` already takes it as a local, and moves the one
`time.monotonic()` call to the single call site in `rate_limit_usage`.

**The refactor: `get_settings()` beside `limiter.policy.limit` (DS-10).**
Not a defect, because both numbers agree today, they come from the same
`Settings` value through `rate_limit_policy()`. A reviewer notices it by
tracking where a value came from rather than only whether it is correct: two
lines in the same function read the request cap two different ways, and the
day someone changes one without the other, the report and the limiter
disagree. Worth a comment phrased as an opportunity, not a blocker.

**The test finding: a private attribute in `test_sweep_drops_keys_older_than_two_windows` (TR-07).**
`limiter._window_start["stale"] = ...` reaches past `hit` and `snapshot` to
set internal state directly. A reviewer catches this by asking, of any test
that touches a name starting with `_`, "is there a public path to this same
setup?" Here there is: the limiter already reads `time.monotonic()` to
decide whether a window is stale, so monkeypatching that call produces the
same fixture through the surface the class actually exposes, and the test
keeps working if the internal representation ever changes.

Two questions an interviewer would ask about these:

1. `format_rate_limit_row` and `seconds_until_reset` are both new public
   functions with no dedicated unit test of their own before the rewrite,
   only the end to end `test_rate_limit_usage_report`. Is that enough
   coverage for a Minor design finding, or would you ask for a direct test
   of each, and what would change your answer?
2. The refactor comment on `get_settings()` says "not urgent." What would
   make it urgent, that is, what is the smallest change to this codebase
   that would make the two numbers actually able to disagree?

## Five questions an interviewer would ask about the rewrite

1. The rewrite guards `hit` and `snapshot` with a single lock on the whole
   limiter. At what request rate does that lock become the bottleneck, how
   would you measure it before believing it, and what is the next design
   after it, striped locks by key hash, a lock free counter, or moving to
   Redis?
2. `get_rate_limiter` holds `_rate_limiter_lock` across `limiter.start()`.
   Defend holding a lock while starting a thread. What would have to be true
   inside `RateLimiter.__init__` or `start` for that to deadlock, and how
   would you notice?
3. The rewrite assigns the module global after `start()` returns rather than
   before. Write the interleaving that the other order allows, with two
   threads and the exact line each is on. Why does moving the assignment fix
   it, given that both threads still run inside the same lock?
4. `sweep` still guards its critical section with `with threading.Lock():`,
   a fresh lock on every call that excludes nobody, and this rewrite does not
   touch it. Why would a reviewer leave that out of this round, and what do
   you say to a teammate who assumes a revised PR has addressed every
   concurrency issue in the file just because it was revised?
5. The rewrite fixes three defects and three design and test findings across
   seven commits, and one of them, adding `now` to `seconds_until_reset`,
   changes a public function's signature. If you had to ship only one commit
   tonight, which one, and what do you tell the on-call engineer about the
   other six?
