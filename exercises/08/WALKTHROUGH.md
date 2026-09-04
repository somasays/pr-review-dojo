# Exercise 08 walkthrough: batch dispatcher for pending confirmations

Mode: teach. Domain: async. Difficulty: easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/13
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/20

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

This PR turns a one-at-a-time send into a fan-out. Everything that changes in
a fan-out is about what happens when one of the parallel things goes wrong,
and about what two of them do to shared state. That tells you the order.

1. **Read the PR description and the file list.** Four source files plus one
   test file. Two of them are new (`handlers.py`, `dispatch.py`), one is a
   four line addition to an existing class (`worker.py`), and one is a real
   change to a shared service (`notification.py`). The shared service is
   where the blast radius is, so it is where you spend your time.
2. **Open `app/services/notification.py` and read `BatchNotifier` last to
   first.** Start at `send_batch`, because that is the new control flow, then
   `_send_one`, which is the new shared state. Reading the fan-out before the
   worker it runs on is deliberate: you want to know what the coroutine
   promises before you look at who calls it.
3. **Then `app/async_tasks/handlers.py`,** and read the `async def` keywords
   before you read the bodies. In this codebase the keyword decides where the
   body runs, so the keyword is the interface.
4. **Then the four added lines in `app/async_tasks/worker.py`.** Small diffs
   in a core class deserve the same attention as large ones.
5. **Then `dispatch.py` and the tests.** By now you know what can go wrong, so
   the only question left is whether the tests would notice.

The one thing worth reading that is not in the diff is the module docstring
of `app/async_tasks/worker.py` and `_invoke` just below it. `_invoke` is nine
lines and it decides the outcome of two of the six findings.

## 2. What to grep for before commenting

- `await ` inside `BatchNotifier`. Three hits. For each one, ask what state
  was read before it and written after it. That question alone finds the
  dedupe defect.
- `asyncio.gather(` across `app/`. Three hits after this PR, and the two that
  existed before are in `worker.py`. Compare the new one with them.
- `iscoroutinefunction` in `app/async_tasks/worker.py`. One hit, in `_invoke`.
  Read it, then go back and reread every `async def` in the diff knowing what
  that branch does.
- `session_scope(` in `app/`. It should appear only in sync call paths. One
  of the new hits is inside a coroutine.
- `get_event_loop\|new_event_loop\|asyncio.run` in `app/`. This codebase had
  zero hits before this PR.
- README conventions 3 (idempotent writes) and 5 (no blocking calls in async
  code). This PR touches both.

## 3. The reasoning chain that surfaces each finding

**The batch that dies on the first failure (Blocker).** Read the signature:
`send_batch` returns `list[Message | BaseException]`. Then read the body: the
only place an exception could get into that list is `gather`, and `gather`
puts exceptions in its result only with `return_exceptions=True`, which is
not there. So the annotation and the code disagree, and the annotation is the
one telling you what the author meant. Now trace the consequence rather than
stopping at "the type is wrong". One `ConnectionError` propagates out of
`send_batch`, out of the handler, into `QueueWorker._handle`, which catches
`Exception` and re-enqueues the task. The next attempt rebuilds the same
message list and sends the ones that already succeeded a second time. That is
what makes it a Blocker: the failure path re-sends real email to real
customers, and the dedupe set that would have stopped it is on the same
object only by luck of the process staying alive. The in-flight sends that
`gather` abandoned finish with nobody recording them.

**The dedupe race (Major).** The mechanical version of the question is: for
every `await`, what did I read before it and write after it? Here the read is
`if message.dedupe_key in self._seen` and the write is
`self._seen.add(message.dedupe_key)`, with the gateway call between them. A
single loop thread does not save you, because an `await` is exactly the point
where another task gets to run. The realistic trigger is in the PR
description: a scheduled pass plus somebody re-running the script by hand.
Both batches enter `_send_one` for the same order, both find an empty set,
both send. Major and not Blocker because the damage is a duplicate email, not
wrong data, and because it needs two overlapping runs rather than one.

**The blocking session in a coroutine (Major).** This one is found by reading
`_invoke` rather than by reading the handler. `_invoke` branches on
`iscoroutinefunction`: a plain `def` gets `asyncio.to_thread`, a coroutine
gets awaited inline. So in this codebase `async def` means "this body runs on
the loop thread", and `session_scope()` plus a query is a blocking call. That
is convention 5 word for word. The consequence to name is not "it is slow",
it is "every other task on the worker, including the sends this batch is
about to start, stops until the query returns". Note the shape of the
mistake: the author wrote `async def` because the body needs `await
notifier.send_batch(...)` at the bottom, which is a genuine reason. The fix
is not to remove the keyword, it is to move the blocking half into a thread.

**The loop borrowed by `drain` (Minor).** Four lines, and the tell is that
`get_event_loop` appears nowhere else in the codebase. Ask where this is
called from. Today, one script on the main thread, where it works. From a
coroutine it raises `This event loop is already running`; from any worker
thread it raises `There is no current event loop`. `asyncio.run` is the
documented way to say "I own the loop" and it fails immediately and clearly
in both of those cases. Minor because the only current caller is fine.

**The two nits.** `ensure_future` instead of `create_task` is a consistency
point with a real if small edge: it returns `Future`, not `Task`, and the
worker's own type annotations are in terms of `Task`. `record_metric` marked
`async def` with a body that awaits nothing costs nothing today and is a trap
the day it grows IO, because `_invoke` will keep running it on the loop and
nothing at the call site changes. Group both at the end and label them as
non-blocking. A review that opens with `ensure_future` has buried the batch
that re-sends email.

## 4. The clean trap

`self.stats.sent += 1` and `self.stats.skipped += 1` sit in a coroutine that
runs many times concurrently with no lock anywhere. That looks like the
textbook race and it is not one. The whole batch runs on a single event loop
thread, and there is no `await` between reading and writing the counter, so
the increment cannot be interleaved. Asserting "these counters race, wrap
them in an `asyncio.Lock`" is a false positive, and the lock it asks for
would serialize nothing useful.

The exercise is built so this sits eight lines away from the `_seen` set,
which is genuinely unsafe. The difference is not the data structure and not
the presence of a lock. It is whether an `await` separates a read from the
write that depends on it. Learn to ask that question instead of pattern
matching on "shared mutable state in async code".

Asking "is `stats` ever touched from a thread?" is free and is not a false
positive. It is also a good question, because the answer (no: sync handlers
run in `to_thread` but never touch `stats`) is what makes the code safe.

## 5. Questions worth asking the author

- What happens when one send in the batch fails? Say it out loud and follow
  it all the way into `QueueWorker._handle`. This single question finds the
  Blocker without reading `gather`'s signature.
- What happens if the scheduled pass and a manual `python -m
  app.async_tasks.dispatch` overlap? This finds the dedupe race.
- `_seen` is per process and unbounded. What is it for after a restart, and
  does the class docstring promise more than a process-local set can deliver?
- The batch has no bound of its own. It inherits one semaphore slot from the
  worker and then starts `--limit` sends at once. At 100 that is fine, at
  20000 it is a gateway incident. Is the limit meant to be the bound?
- Four new tests, none of which makes a send fail and none of which puts two
  batches in flight. Which of those two would have caught the most expensive
  bug here?

## 6. Five interviewer questions about the rewrite

1. The fix claims the dedupe key before the await and discards it in an
   `except BaseException`. Why `BaseException` and not `Exception`, and what
   would break if a cancelled send left the key claimed?
2. `asyncio.to_thread` moved the query off the loop, but the handler is still
   `async def`. Walk through what `QueueWorker._invoke` does with it now, and
   explain why making the handler a plain `def` would have been worse.
3. `return_exceptions=True` means `send_batch` never raises. Who is
   responsible for retrying a failed message now, and what would you have to
   change in `_handle` for the worker to retry only the failures?
4. `drain()` became `asyncio.run`. Name a caller that worked before this
   change and now raises, and argue whether that is an improvement.
5. Each of these six commits is a few lines. Which one would you have pushed
   back on as unnecessary in a real review, and what is the argument for
   shipping the two nits in the same PR rather than leaving them?
