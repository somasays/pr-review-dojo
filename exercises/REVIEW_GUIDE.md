# Review guide

The four steps for any pull request, sized for a 30 to 40 minute review.
Deliverable of each step feeds the next. Write things down as you go; the
summary at the end is assembled from those notes, not written from memory.

## Step 1: orient (5 minutes, no diff bodies yet)

```
gh pr view <n> --json title,body --jq '"\(.title)\n\n\(.body)"'
git diff --stat main...HEAD
```

1. What does the author claim? One sentence. Note what the description promises and what it is silent about.
2. Map every file to a layer (api, service, domain, db, jobs, config, tests, docs). Mark the file with the most added lines: the center of gravity.
3. Find the entry points: grep the repo for the new module or function name. The callers tell you the runtime context (request threadpool, startup, background thread, Spark executor), and context decides which bug classes are possible.
4. Note what is absent: test file, README line, migration, validation.
5. Write the narration hypothesis: two to four sentences on what the change does, where it sits, what it depends on.

## Step 2: the center, read as a component (10 to 15 minutes)

Open the center-of-gravity file in full, not the diff view.

For a logic-heavy change (domain code, pricing, dates, transforms), do not
start at the biggest file. Start at the entry point and build a call tree:

1. Find who calls the changed module from an outer layer: `grep -rn "from app.domain.<mod> import" app/services app/api`. That call is the entry point.
2. From it, write the call tree top-down, one line per function, with what each produces, from names and docstrings only. List what is in the diff but not in the tree: orphan functions are a question for the author.
3. Review leaves first with a boundary table (exactly at the threshold, one below, one above, zero, empty, negative), then the composer with the pipeline pass: list its assignments in order ignoring the `if`s, write the invariant each value must satisfy, grep for where each invariant is enforced, and build one input that gets past the unenforced one. Run it by hand and follow the bad value downstream until something rejects or persists it.
4. In this codebase `app/domain` helpers are public so they can be unit-tested; public does not mean entry point.

1. Outline it: `grep -n "^class \|^def \|    def \|self\._[a-z_]* =" <file>`. The `def` lines are the surface, the `self._x =` lines are the state.
2. Trace one common call end to end, one line per hop, recording what happens to each piece of state.
3. Trace the lifecycle: how many instances per process, created when, started and stopped how. Write it as one sentence.
4. For a stateful component: which methods touch each state field, and what protects it when two run at once? `grep -n "def \|<state names>\|<lock name>" <file>` gives an outline where every `def` block should show a lock line above its state lines.
5. Boundaries for each method: first call ever, unknown key, exactly the limit, one past it, empty input.
6. Record findings as scratch lines with a line number and a guessed severity. Do not write comments yet.

Recurring patterns to grep for:

| Grep | Question |
| --- | --- |
| `sleep`, `daemon`, `join`, `Event` | What wakes this thread early, what tells it to exit, and where is that checked? A polled flag after a sleep is a condition variable never written. |
| `global`, module-level `= None` | Lazy singleton: is the check-then-create under a lock? |
| `Lock()` inside a method | A new lock per call locks nothing. |
| `get(...) + 1`, `+=` on shared state | Read-modify-write without a lock. |
| `commit()` | Who owns the transaction? Repositories and services never commit here. |
| `f"` near `execute`, `text(` | SQL built from strings. |
| `now()`, `today()`, `get_settings()` inside logic | Clock or config read where a parameter should be injected. |
| `except Exception` | Swallowed or re-wrapped without `from`. |
| `.collect()`, `toPandas()`, no `dt` filter | Driver memory, full scans. |

## Step 3: the layers around the center (10 minutes)

One question per surrounding file. Read tests last.

1. Wiring (deps, DI, main): how many instances, created when, on which thread; what is the key or identity; what runs before it and does the order matter.
2. Router or job entry: which paths got the change and which did not; if the split is not obviously intentional, it is a question for the author.
3. Config: defaults, and what zero, negative, or missing values do downstream.
4. Docs: does the documented contract match the code (semantics, caveats, error responses)? A missing deployment caveat is a cross-team point.
5. Tests, read as code under review: for each finding, would any test have caught it? Which paths are never exercised (concurrency, exact boundary, failure path, shutdown)? Does any test reach into private state, sleep, or depend on the wall clock? Test gaps are findings.

This step turns component correctness into system correctness: runtime context makes races real or moot, contracts expose mismatches, callers show blast radius, and tests show which findings the author could have caught.

## Step 4: severity, order, comments (5 to 10 minutes)

1. Merge by root cause. One race with three symptoms is one comment with "also at L..".
2. Severity, one question per level, in order: Blocker loses data, exposes data, or takes the service down. Major is wrong under realistic conditions or a risky change without its test. Minor is maintainability, small perf, or a misleading name. At most one Nit. Design findings are Major when the next feature or test pays for it. Test findings are Major when the untested path is the one the change exists for.
3. Order by what the author should do first: blocking, then items sharing a fix, then Minor, then questions.
4. Each inline comment, two to five sentences, tag first: observation with the triggering input, consequence in production, fix or the evidence that would settle it. Tags: `[Blocker]`, `[Major]`, `[Minor]`, `[Refactor]`, `[Test]`. Questions carry no tag.
5. Summary in three blocks: narration (corrected from step 1), decision with one sentence of why, priority list pointing at the comments, ending with open questions and what to watch after deploy.
6. Re-read as the author. Cut "I would have done it differently". Every assertion needs a consequence; the ones without are questions or noise. No "obviously", no "just".

## Step 5: after the grade

Sort misses into "did not look there" and "looked and did not see". The first kind becomes a step 1 or 3 question; the second kind becomes a step 2 grep.
