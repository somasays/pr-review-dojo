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

**`with threading.Lock():` in `sweep` (line 69).** The eye reads `with`,
`Lock`, colon, and files it as guarded. The trick to catching it is to always
ask *which* lock, not *whether* there is one. A lock only means something if
two threads can name the same object, and `threading.Lock()` names a new one
each time. Once you see it, keep going, because the interesting part is the
consequence, not the mistake. `sweep` iterates `_window_start` inside the
block. `hit` inserts into `_window_start` with no lock at all. A dict mutated
during iteration raises `RuntimeError`, `_run` has no `try`, so the sweeper
thread dies the first time a new API key arrives during a sweep and never
comes back. The feature that fails is not rate limiting, it is the unbounded
memory growth the sweeper was added to prevent. A reviewer who stops at "this
lock is wrong" has found the defect; a reviewer who gets to "the sweeper dies
silently and the map grows forever" has written the comment that gets it
fixed today.

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

**`time.sleep` in `_run` (line 87).** Easy to spot, easy to under-argue.
The comment that lands is not "use an Event because Events are better", it is
"`stop()` currently does nothing for up to a window, so shutdown hangs and no
test can watch this loop without waiting a minute". The fix removes a piece
of state rather than adding one, which is the tell that it is the right fix.

**The unnamed thread and the atomicity comment.** Both Nits, both worth
leaving. The comment one is worth more than it looks: it is the only place in
the diff where the author's incorrect mental model is written down in
English, and if it survives the fix it will produce this bug again in the
next service.

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

## Five questions an interviewer would ask about the rewrite

1. The rewrite guards `hit`, `snapshot` and `sweep` with a single lock on the
   whole limiter. At what request rate does that lock become the bottleneck,
   how would you measure it before believing it, and what is the next design
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
4. `sweep` now holds the limiter lock while iterating every key in
   `_window_start`. With a hundred thousand distinct API keys, that blocks
   every request for the duration of the iteration. Is the alternative,
   iterating a snapshot outside the lock and taking the lock only for the
   pops, better or worse, and what does it change about correctness?
5. The rewrite fixes six things and changes no public signature and no test.
   If you had to ship only one commit tonight, which one, and what do you
   tell the on-call engineer about the other five?
