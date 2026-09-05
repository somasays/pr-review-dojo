# Async defect catalog

Defects for the asyncio domain. Each entry names a real file and function in this codebase, a plausible feature PR that would touch it, what the mistaken code looks like, and what the hidden test under `solutions_tests/` asserts. `/exercise` and `/seed` pick entries from here by severity mix, plant them so the diff reads as honest feature work, and plant one pattern from "Looks wrong but is fine" as the clean-code trap. Graders match a reviewer's comment to an entry by root cause, so descriptions state the mechanism, not just the symptom. The worker under test is `app/async_tasks/worker.py` (`QueueWorker`), the sender is `app/services/notification.py`, and `tests/test_worker.py` runs with pytest-asyncio in auto mode, so hidden tests are plain `async def` functions.

## Do not plant

- Trivia (a linter's job, not a reviewer's): AS-20, AS-21, AS-22
- Internals (deeper than a generalist interview goes): AS-12, AS-13, AS-15, AS-17, AS-18

Everything else is the middle band a strong generalist is expected to reason about. Pick from it.

## Defects

### AS-01: Retry backoff sleeps the event loop
- Severity: Blocker
- Description: A coroutine calls `retry()` with its default `time.sleep`, so every backoff freezes the whole worker loop instead of one task.
- Planting: Feature: an async batch notifier. Add `async def send_batch(self, messages)` to `NotificationService` in `app/services/notification.py` that loops `for m in messages: retry(lambda: self.sender.send(m), self.policy)` directly in the coroutine. The sync `_deliver` passes `sleep=lambda _s: None`; the new method forgets that argument and also skips `asyncio.to_thread`, so the default `time.sleep` in `app/services/retry.py` runs on the loop thread for `backoff_seconds` per attempt.
- Hidden test: Build a `NotificationService` over `InMemorySender(fail_times=2)` with `notify_backoff_seconds=0.2`. Run `send_batch([msg])` concurrently with a heartbeat task that appends `perf_counter()` every 10 ms. Assert the largest gap between heartbeats is under 50 ms; on the defect it exceeds 200 ms. Alternatively enable `loop.set_debug(True)` with `slow_callback_duration=0.05` and assert no "Executing <Task" warning from the `asyncio` logger.

### AS-02: Sync database session used directly inside an async handler
- Severity: Major
- Description: A coroutine handler opens `session_scope()` and runs queries on the loop thread, breaking convention 5 and stalling every other task while the database responds.
- Planting: Feature: a periodic stock sync task kind. Register `async def sync_stock(payload)` with the worker in a new `app/async_tasks/handlers.py` that does `with session_scope() as db: for p in ProductRepository(db).by_skus(payload["skus"]).values(): p.stock = ...`. It is `async def` because a sibling handler awaits an HTTP client, so the author kept both async; `QueueWorker._invoke` sees a coroutine function and awaits it inline rather than through `to_thread`.
- Hidden test: Patch `session_scope` with a context manager that calls `time.sleep(0.3)` on enter. Enqueue one `sync_stock` task and four `async def` no-op tasks with `concurrency=4`, drain with `run_until_idle`, and assert the no-ops all complete within 100 ms of being dequeued (record `perf_counter()` in each). On the defect they wait behind the sleep.

### AS-03: `gather` without `return_exceptions` drops the rest of the batch
- Severity: Blocker
- Description: `asyncio.gather(*sends)` raises on the first failed send, so the caller never sees results for the others, marks the batch failed, and the surviving sends run unobserved.
- Planting: Feature: the batch notifier from AS-01 fans out with `results = await asyncio.gather(*(self._send_async(m) for m in messages))` and then records successes into `WorkerStats.processed`. One `ConnectionError` propagates out of `gather`, the per-message accounting after it never runs, and the `_handle` retry path re-enqueues the entire batch, so already-delivered messages are sent again on the next attempt. The dedupe key protects the customer, but the stats and the retry budget are wrong.
- Hidden test: Sender fails exactly once on the second of three messages. Call `send_batch` and assert it returns a per-message result list with two successes and one exception, and that `sender.sent` has length two. On the defect the call raises `ConnectionError` and the caller cannot tell which messages went out.

### AS-04: Fan-out with `create_task` in a loop and no bound
- Severity: Blocker
- Description: A loop creates one task per order with no semaphore, so a large batch opens thousands of gateway calls at once and bypasses the worker's `WORKER_CONCURRENCY` guarantee.
- Planting: Feature: a "resend confirmations for all paid orders" admin task. The handler does `for order in orders: tasks.append(asyncio.create_task(self._send_async(order)))` then `await asyncio.gather(*tasks)`. The author reasoned the outer task already holds a semaphore slot, so inner work is "already bounded". Realistic load: 20k paid orders, one 429 storm from the gateway, retries multiply it.
- Hidden test: Replace the sender with an async stub that increments an `active` counter, awaits `asyncio.sleep(0.01)`, tracks `peak`, and decrements. Feed 50 orders and assert `peak <= worker.sem._value` at construction (or a documented inner bound such as 8). On the defect `peak == 50`.

### AS-05: Fire-and-forget tasks are not kept referenced
- Severity: Blocker
- Description: `create_task` results are discarded, so the event loop holds only weak references and tasks can be garbage collected mid-flight, and shutdown has nothing to wait on.
- Planting: Feature: simplify `QueueWorker.run` while adding a metrics hook. The refactor replaces the `_inflight` bookkeeping with `asyncio.create_task(self._handle(task))` and nothing else, keeping `self._inflight` only for `run_until_idle`'s emptiness check (now always empty). The final `if self._inflight: await asyncio.gather(...)` still compiles and looks intact.
- Hidden test: Register a handler that awaits `asyncio.sleep(0.2)` then appends to `seen`. Enqueue three tasks, call `worker.stop()` right after the first dequeue, await `run()`, and assert `len(seen) == 3` immediately after `run()` returns. On the defect `run()` returns before the handlers finish, and with `gc.collect()` inserted during the sleep some never finish at all.

### AS-06: Shutdown cancels in-flight work instead of awaiting it
- Severity: Blocker
- Description: `run()` now cancels outstanding tasks on stop, so a handler that has flushed a status change but not yet sent its notification is killed between the two.
- Planting: Feature: "fast shutdown" for container restarts. In `QueueWorker.run`, the tail becomes `for t in self._inflight: t.cancel()` followed by `await asyncio.gather(*self._inflight, return_exceptions=True)`. The module docstring still says shutdown waits for in-flight tasks. For an async `mark_paid` handler this cancels after the commit and before `order_confirmed`, and the task is not re-enqueued because `_handle` re-raises `CancelledError`.
- Hidden test: Handler awaits `asyncio.sleep(0.1)` then records completion. Enqueue two, call `stop()` after the first `get`, await `run()` with a 2 s guard, and assert both completed and `stats.processed == 2`. On the defect completion count is zero or one.

### AS-07: `CancelledError` swallowed and the task re-enqueued
- Severity: Major
- Description: Catching `BaseException` in the retry path treats cancellation as a transient failure, so a cancelled task is put back on the queue and shutdown never converges.
- Planting: Feature: "retry on any failure, including `KeyboardInterrupt` from a handler thread". In `QueueWorker._handle` the author removes the explicit `except asyncio.CancelledError: raise` and widens `except Exception` to `except BaseException`. Cancellation now increments `stats.retried`, re-puts the task, and the `while not (stop and queue.empty())` loop in `run()` sees a non-empty queue forever.
- Hidden test: Handler awaits `asyncio.sleep(10)`. Start `run()` as a task, enqueue one item, wait 50 ms, cancel the `_handle` task found via `worker._inflight`, then `stop()` and `await asyncio.wait_for(run_task, 1)`. Assert it returns, `stats.retried == 0`, and `queue.qsize() == 0`. On the defect `wait_for` times out and `stats.retried == 1`.

### AS-08: Retry re-enqueue is missing `await`
- Severity: Blocker
- Description: `self.queue.put(...)` without `await` creates a coroutine and drops it, so failed tasks are counted as retried but never run again.
- Planting: Feature: attach a `retry_delay` to `Task` and add backoff before re-enqueue. While reordering the lines in `QueueWorker._handle`, the re-put becomes `self.queue.put(Task(task.kind, task.payload, attempt=task.attempt + 1))` with no `await`. Python emits "coroutine 'Queue.put' was never awaited" only at garbage collection, which `log.warning` noise hides in a real run.
- Hidden test: The existing `test_retries_then_fails` shape: handler raises every time, `max_attempts=3`. Assert `len(attempts) == 3` and `stats.failed == 1`. On the defect `attempts == 1`, `stats.retried == 1`, and `pytest -W error::RuntimeWarning` also fails on the unawaited coroutine.

### AS-09: `wait_for` around `to_thread` retries a handler that is still running
- Severity: Major
- Description: A timeout cancels the awaiting coroutine but not the thread, so the sync handler keeps running while the worker schedules a retry, producing duplicate non-idempotent side effects.
- Planting: Feature: per-task timeout. `QueueWorker._invoke` becomes `await asyncio.wait_for(asyncio.to_thread(handler, payload), timeout=self.task_timeout)` and `_handle` catches `TimeoutError` in the generic `except Exception` branch, which re-enqueues. A slow `OrderService.create` handler that decrements `Product.stock` then completes in its thread, and the retry decrements it again. Convention 10: retry only idempotent operations.
- Hidden test: Sync handler does `time.sleep(0.3)` then appends to `calls`. Set `task_timeout=0.1`, `max_attempts=3`, enqueue one, drain, then `await asyncio.sleep(1)` and assert `len(calls) == 1` and the task is recorded as failed once rather than retried. On the defect `len(calls) == 3`.

### AS-10: Check-then-act on a shared dedupe set across an `await`
- Severity: Major
- Description: A membership test on a shared set happens before an `await`, and the insert after, so two tasks with the same dedupe key both pass the check and both send.
- Planting: Feature: in-process dedupe for the async notifier. `send_batch` keeps `self._seen: set[str]` and does `if m.dedupe_key in self._seen: continue; await self._send_async(m); self._seen.add(m.dedupe_key)`. The author notes "single-threaded loop, no lock needed", which is true for the counter case (see AS-CLEAN-01) but not with an `await` between check and insert.
- Hidden test: Async sender stub awaits `asyncio.sleep(0.05)` per send and records calls. Run two `send_batch` calls concurrently with the same message via `gather`. Assert exactly one send was made. On the defect both go out. The fix is either add before await or hold an `asyncio.Lock` around the pair.

### AS-11: No timeout on the gateway call
- Severity: Major
- Description: The async sender awaits an external HTTP call with no timeout, so a hung gateway pins semaphore slots and the worker silently stops making progress.
- Planting: Feature: `AsyncHttpSender` in `app/services/notification.py` using `httpx.AsyncClient(timeout=None)` (or aiohttp with defaults) and `await self.client.post(self.url, json=...)`. The `RetryPolicy.retry_on` includes `TimeoutError`, so the author assumed timeouts are handled, but nothing ever raises one.
- Hidden test: Point the sender at an ASGI app whose handler awaits `asyncio.sleep(5)`. Call `send` under `asyncio.wait_for(..., 2)` and assert a `TimeoutError` (or `httpx.TimeoutException`) surfaces from the sender within 2 s. On the defect the outer `wait_for` fires instead, and a second assertion that `worker.sem._value` returns to its initial count after draining fails.

### AS-12: `task_done()` called twice per task
- Severity: Minor
- Description: A second `task_done()` inside `_handle` on top of the existing done callback over-counts, so `queue.join()` raises `ValueError` from the callback and joins become unusable.
- Planting: Feature: switch `run_until_idle` to `await self.queue.join()` for a cleaner idle check, and add `finally: self.queue.task_done()` at the bottom of `_handle` "so retries balance". The existing `t.add_done_callback(lambda _t: self.queue.task_done())` in `run()` stays. The retry path also re-puts a task, which the join now waits for correctly, masking the double call until the first failure.
- Hidden test: Enqueue two tasks with a no-op handler, drain, then assert `queue._unfinished_tasks == 0` and that no `ValueError` was reported to the loop exception handler (install a handler that appends to a list). On the defect the list holds "task_done() called too many times".

### AS-13: `functools.partial` around an async handler is dispatched to a thread
- Severity: Major
- Description: `asyncio.iscoroutinefunction` returns False for a partial of a coroutine function, so `to_thread` calls it, gets a coroutine object back, and drops it; the handler never runs but is counted as processed.
- Planting: Feature: handler registration with bound settings. `app/async_tasks/handlers.py` registers `worker.register("confirm", functools.partial(confirm_async, settings=settings))` where `confirm_async` is `async def`. `QueueWorker._invoke` falls through to `asyncio.to_thread(handler, payload)`, which returns the unawaited coroutine, and `stats.processed += 1` follows.
- Hidden test: Register `partial(ahandler, tag="x")` where `ahandler` is async and appends to `seen`. Enqueue one, drain, assert `seen == [("x", ...)]`. On the defect `seen == []` while `stats.processed == 1`, and `-W error::RuntimeWarning` reports "coroutine was never awaited". Fix: `inspect.iscoroutinefunction` handles partials since 3.8, or check the return value with `inspect.isawaitable`.

### AS-14: New event loop created per call and never closed
- Severity: Minor
- Description: A sync helper builds `asyncio.new_event_loop()` and `run_until_complete` on every call, leaking a loop and its selector each time and ignoring any loop that already exists.
- Planting: Feature: let the sync `OrderService.mark_paid` push a task onto the async notifier queue. A helper `_run(coro)` in `app/services/order_service.py` does `loop = asyncio.new_event_loop(); return loop.run_until_complete(coro)` with no `loop.close()` and no `asyncio.set_event_loop`. It works from FastAPI's threadpool, so tests pass, and each request leaves a `ResourceWarning: unclosed event loop`.
- Hidden test: Call `mark_paid` 50 times under `warnings.catch_warnings(record=True)` with `simplefilter("always")` and `gc.collect()`, and assert no `ResourceWarning` mentioning "event loop". Also assert `mark_paid` works when invoked through `asyncio.to_thread` from a coroutine, which the fix (a thread-safe `queue.put_nowait` via `loop.call_soon_threadsafe`, or a plain sync queue) satisfies.

### AS-15: Async generator holds a session until garbage collection
- Severity: Minor
- Description: An async generator yields orders from an open session, and a consumer that breaks early leaves the session open until the generator finalizer runs.
- Planting: Feature: stream pending orders to the notifier. `async def iter_pending(db)` in `app/async_tasks/handlers.py` does `for o in await asyncio.to_thread(OrderRepository(db).list_by_status, OrderStatus.PENDING_PAYMENT): yield o` inside a `with session_scope() as db` opened in the generator body. The consumer does `async for o in iter_pending(): if limit_reached: break` without `contextlib.aclosing`, so the `with` never exits deterministically.
- Hidden test: Patch `session_scope` to record enter and exit. Iterate `iter_pending()` under `aclosing`, break after the first item, and assert exit was recorded before the consumer's next line runs. On the defect exit is recorded only after an explicit `gc.collect()` or at loop shutdown. Fix: make the caller use `aclosing`, or move the session out of the generator.

### AS-16: FastAPI `async def` handler makes a sync HTTP call
- Severity: Major
- Description: A coroutine route calls `requests.get` on the event loop thread, so a slow upstream stalls every request in the process instead of one threadpool worker.
- Planting: Feature: a deep health check in `app/api/main.py`. `health` becomes `async def health()` that does `requests.get(settings.gateway_url + "/ping", timeout=2)` and returns `{"status": "ok", "gateway": r.status_code}`. It is `async def` because the author also awaits `asyncio.sleep(0)` for a "yield to other requests" comment. The sync `def health` it replaced ran in FastAPI's threadpool.
- Hidden test: Use `httpx.AsyncClient` with `ASGITransport` against `create_app()`. Patch `requests.get` to `time.sleep(0.5)`. Fire `/health` and five `/customers/me` requests concurrently with `gather` and assert the five complete within 200 ms. On the defect they all finish after 500 ms. A `loop.set_debug(True)` slow-callback warning check is an equivalent assertion.

### AS-17: `run_until_complete` on `get_event_loop()` from a script helper
- Severity: Minor
- Description: A sync convenience method fetches the current loop and drives it, which raises "This event loop is already running" when called from any coroutine and warns under 3.12 when no loop exists.
- Planting: Feature: `QueueWorker.drain()` "for scripts" that does `asyncio.get_event_loop().run_until_complete(self.run_until_idle())`. The new `python -m app.async_tasks.worker` entrypoint calls it and works. A follow-up test calls `worker.drain()` from an `async def` test and crashes.
- Hidden test: From an `async def` test, call `await asyncio.to_thread(worker.drain)` and also `worker.drain()` directly wrapped in `pytest.raises` on the defect. The fix (make `drain` `asyncio.run(self.run_until_idle())` documented as script-only, or drop it) passes the `to_thread` variant and emits no `DeprecationWarning`.

### AS-18: `stop()` called from a foreign thread
- Severity: Minor
- Description: `asyncio.Event.set()` is invoked from a thread that is not the loop thread, which is not thread safe and relies on the poll timeout to be noticed.
- Planting: Feature: an admin "drain" endpoint served by a small `threading.Thread` HTTP server alongside the worker in the `__main__` block. Its handler calls `worker.stop()` directly. It appears to work because `run()` polls with `poll_timeout=0.1`.
- Hidden test: Start `run()` on a fresh loop in a thread, call `worker.stop()` from the test thread, and assert the loop exits within 2x `poll_timeout` with `loop.set_debug(True)` reporting no "Non-thread-safe operation" error via the loop exception handler. Fix: `QueueWorker.stop_threadsafe()` that captures the loop in `run()` and uses `call_soon_threadsafe(self._stop.set)`.

### AS-19: Uncaught `Exception` inside `run_until_idle`'s watcher kills the worker
- Severity: Major
- Description: The idle watcher raises on a stats hook, and `gather(self.run(), watch())` propagates it while `run()` keeps going as an orphaned task that is then cancelled by the loop shutdown.
- Planting: Feature: emit a metrics line on idle. `watch()` in `QueueWorker.run_until_idle` calls `self.on_idle(self.stats)` where `on_idle` is an optional user callback. A callback that raises (for example a StatsD client with a closed socket) ends `run_until_idle` with the exception while `run()` is still draining, so `asyncio.run` cancels the in-flight handlers.
- Hidden test: Set `on_idle=lambda s: 1 / 0` and register a handler that sleeps 0.2 s. Enqueue three, call `run_until_idle`, and assert `stats.processed == 3` and the `ZeroDivisionError` is logged, not raised. On the defect `run_until_idle` raises and processed is below three. Fix: wrap the callback in `try/except Exception: log.exception(...)`.

### AS-20: `asyncio.ensure_future` where the codebase uses `create_task`
- Severity: Nit
- Description: `ensure_future` accepts any awaitable and returns a `Future`, which loses the `Task` type used by `_inflight: set[asyncio.Task[None]]` and reads as legacy style next to the existing `create_task` call.
- Planting: In the batch notifier, `futs = [asyncio.ensure_future(self._send_async(m)) for m in messages]`. Works identically for coroutines.
- Hidden test: None beyond mypy staying green. The exercise's `ruff` config with the `ASYNC` rules flags nothing, so this is graded on the review only.

### AS-21: Handler declared `async def` with a fully synchronous body
- Severity: Nit
- Description: A handler that awaits nothing is marked `async def`, so `_invoke` runs it inline on the loop instead of in a thread; harmless while the body is trivial, a trap once someone adds a database call to it.
- Planting: `async def record_metric(payload)` in `app/async_tasks/handlers.py` that only appends to an in-memory list. The `test_dispatches_sync_and_async_handlers` pattern makes this look normal.
- Hidden test: None. Reviewer credit for pointing out that a sync body should be a plain `def` so that `_invoke` routes it through `to_thread` if it ever grows IO.

### AS-22: `asyncio.TimeoutError` caught where the file uses builtin `TimeoutError`
- Severity: Nit
- Description: A new `except asyncio.TimeoutError` next to the existing `except TimeoutError` in `run()` is inconsistent; the names are the same class since 3.11 but the mix suggests the author thinks they differ.
- Planting: In the per-task timeout branch added for AS-09, `except asyncio.TimeoutError as exc:`. Behavior is unchanged.
- Hidden test: None. Graded on the review only.

## Looks wrong but is fine

### AS-CLEAN-01: Counter increments on `WorkerStats` without a lock
- Pattern: `self.stats.processed += 1` and `self.stats.errors.append(...)` in `QueueWorker._handle`, called from many concurrent tasks with no `asyncio.Lock`.
- Why it is fine: Every task runs on one loop thread and there is no `await` between the read and the write of the counter, so no other task can interleave. Sync handlers run in `to_thread` but never touch `stats`; the update happens back on the loop after the await returns. `test_concurrency_bound` and `test_retries_then_fails` depend on this and pass at `concurrency=3`.
- What a reviewer might wrongly say: "Race condition on `stats.processed`, wrap it in an `asyncio.Lock`" or "this counter needs to be atomic because handlers run in threads".

### AS-CLEAN-02: `asyncio.Semaphore` and `asyncio.Event` created in `__init__` with no running loop
- Pattern: `QueueWorker.__init__` builds `self.sem = asyncio.Semaphore(concurrency)` and `self._stop = asyncio.Event()`, and tests construct the worker inside `async def` but scripts construct it before `asyncio.run`.
- Why it is fine: Since Python 3.10 asyncio primitives bind to a loop lazily on first use, and the `loop` argument was removed. The codebase is 3.12. Construction outside a loop is the documented pattern, and reusing one worker across two `asyncio.run` calls is the real hazard, which nothing here does.
- What a reviewer might wrongly say: "Creating a Semaphore outside the event loop raises `RuntimeError: no running event loop`" or "the semaphore is bound to the wrong loop".

### AS-CLEAN-03: `except TimeoutError` around `asyncio.wait_for`
- Pattern: `run()` catches the builtin `TimeoutError` after `await asyncio.wait_for(self.queue.get(), timeout=self.poll_timeout)`.
- Why it is fine: `asyncio.TimeoutError` has been an alias of the builtin `TimeoutError` since 3.11, so the clause catches exactly what `wait_for` raises. `test_dispatches_sync_and_async_handlers` exercises the timeout path on every idle poll.
- What a reviewer might wrongly say: "This catches the wrong exception, `wait_for` raises `asyncio.TimeoutError` so the timeout will propagate and kill the worker".

### AS-CLEAN-04: Sync `OrderService` handler opening `session_scope()` while registered with the async worker
- Pattern: `def pay(payload): with session_scope() as db: OrderService(db, PricingService(), NotificationService(sender)).mark_paid(payload["order_id"])` registered via `worker.register("pay", pay)`. Plain `def`, sync session, sync `retry` with `time.sleep` backoff inside `NotificationService`.
- Why it is fine: `QueueWorker._invoke` routes every non-coroutine handler through `asyncio.to_thread`, which is exactly what convention 5 prescribes: sync handlers run in a thread so the loop is never blocked. The module docstring for `worker.py` states this contract, and `test_blocking_handler_does_not_block_loop` proves a 200 ms sleep in a sync handler does not delay siblings. Only a coroutine handler doing the same work (AS-02) is a defect.
- What a reviewer might wrongly say: "Blocking database call in async code, violates convention 5, convert to an async session" or "`time.sleep` in `retry` will block the worker".
