# Concurrency defect catalog

Defects for exercises about threads and process-level shared state: the module
level singletons in `app/api/deps.py` and `app/db/session.py`, the `lru_cache`
caches in `app/services/config.py` and `app/db/session.py`, and the background
threads a feature PR adds around `app/services/notification.py` and
`app/services/order_service.py`. FastAPI runs every `def` handler in this
codebase (all routers are sync) on a threadpool, so anything defined at module
scope is shared across request threads and every entry here is reachable from
two concurrent requests. `/exercise` and `/seed` pick entries by id, plant them
inside an honest-looking feature PR using the Planting notes, and write the
hidden test described under Hidden test into `solutions_tests/` on the solution
branch. Three features carry most of these and can be combined without the diff
looking staged: an in-process rate limiter keyed by API key in `app/api/deps.py`,
a stock reservation cache consulted by `OrderService.create`, and a batching
notification flusher thread wrapping `Sender`. Severity follows the scale in
`CLAUDE.md`: production impact at realistic load decides, not how hard the
defect is to spot. Hidden tests must be deterministic and fast, so they force
interleavings with a `threading.Barrier`, an injected `threading.Event`, or a
monkeypatched hook rather than sleeping and hoping. Every exercise also plants
one entry from the "Looks wrong but is fine" section; a reviewer who asserts a
defect there earns a false positive.

## Defects

### CC-01: Check-then-act on stock lets two threads oversell the last unit
- Severity: Blocker
- Description: `ReservationCache.reserve` checks `product.stock - self._held(sku) >= quantity` and then
  adds the reservation as two separate steps with no lock, so two request threads that both pass the check
  for the last GADGET both reserve it, `OrderService.create` decrements `Product.stock` twice, and stock
  goes negative while a customer is promised inventory that does not exist.
- Planting: New `app/services/reservations.py` with a module level `_cache = ReservationCache()`,
  consulted from `OrderService.create` right before the `products[i.sku].stock -= i.quantity` loop.
  Feature: "hold stock for 10 minutes while the customer completes payment". The mistaken body is `if
  self.available(sku, stock) >= quantity: self._held[sku] = self._held.get(sku, 0) + quantity; return
  True` with `self._lock` acquired only inside `available`, so each step is individually locked and the
  pair is not.
- Hidden test: Build the cache with GADGET stock 5. Run 5 threads from a `ThreadPoolExecutor` released
  together by a `threading.Barrier(5)`, each calling `reserve("GADGET", 2)`. Assert the number of `True`
  results is exactly 2 and `sum(cache._held.values()) <= 5`. Repeat the whole run 20 times inside the test
  so the interleaving is hit reliably. The defect grants three or more reservations in at least one round.

### CC-02: Two locks acquired in opposite orders deadlocks the request threads
- Severity: Blocker
- Description: `NotificationFlusher.flush` takes `self._queue_lock` then `self._batch_lock` while
  `NotificationFlusher.enqueue` takes `self._batch_lock` then `self._queue_lock`, so a request thread
  enqueuing while the background thread flushes deadlocks both, and every later request that touches
  notifications piles up behind them until the process is restarted.
- Planting: In `app/services/notification.py`, new `NotificationFlusher` with two locks, one guarding the
  pending queue and one guarding the batch being assembled. Feature: "batch confirmation emails so the
  gateway sees one call per 50 messages". The two methods are written weeks apart in the author's head and
  read fine in isolation; only the acquisition order differs. Nothing in the diff acquires both locks in
  one visible place.
- Hidden test: Start the flusher thread, then from two threads released by a `threading.Barrier(2)` call
  `enqueue` and `flush` in a loop for 200 iterations each. Join both threads with `timeout=5` and assert
  `not t.is_alive()` for each. The defect leaves at least one thread alive and the test fails without
  hanging the suite. Optionally also monkeypatch both locks with a wrapper that records `(thread name,
  lock name)` acquisitions and assert no two threads hold them in different orders.

### CC-03: Background flusher keeps the request's SQLAlchemy Session
- Severity: Blocker
- Description: `get_order_service` hands the request scoped `Session` from `get_db` to the process wide
  flusher, which uses it from its own thread after the request has returned and `get_db`'s `finally:
  session.close()` has run, so the flusher works on a closed Session shared with later requests: identity
  map corruption, `sqlalchemy.exc.InvalidRequestError` on concurrent flushes, and emails built from
  another customer's rows.
- Planting: In `app/api/deps.py`, `get_order_service`. Feature: "look up the customer email in the flusher
  instead of passing it through the payload". The author writes `_flusher.session = db` (or
  `NotificationFlusher(db)` cached at module scope) so the flusher can call
  `CustomerRepository(...).get(...)`. It reads as reuse of an existing dependency. The correct shape is
  `session_scope()` inside the flusher thread, as `app/db/session.py` documents and `AS-CLEAN-04` in the
  async catalog describes.
- Hidden test: Use the `session_factory` and `seeded` fixtures. Build the flusher the way
  `get_order_service` does, close the session the request would close, then call the flusher's work method
  from a `ThreadPoolExecutor` with 4 workers and assert it completes without raising and that a fresh
  `Session` from the factory sees the expected rows. Also assert by attribute inspection that the flusher
  holds no `Session` (`not any(isinstance(v, Session) for v in vars(flusher).values())`), so a fix that
  only adds a lock still fails.

### CC-04: Lazy singleton built without a lock starts two flusher threads
- Severity: Blocker
- Description: `get_flusher()` does `global _flusher; if _flusher is None: _flusher =
  NotificationFlusher(); _flusher.start()`, so two request threads arriving on a cold process both see
  `None`, both construct, and both start a thread; one flusher is orphaned but still draining, so every
  confirmation email goes out twice and the second flusher's queue is invisible to the code that later
  calls `stop()`.
- Planting: In `app/api/deps.py`, next to the existing `_sender = InMemorySender()`. Feature: "start the
  flusher lazily so importing the app does not spawn threads in tests". The author mirrors the existing
  module level singleton style but misses that `_sender` is built at import time under the import lock
  while this one is built during a request. `lru_cache` on `get_settings` in `app/services/config.py` is
  the pattern that would have been safe.
- Hidden test: Reset the module global to `None`, then call `get_flusher` from 8 threads released by a
  `threading.Barrier(8)`. Assert every returned object `is` the first one and that the count of live
  threads whose name starts with the flusher's name is exactly 1. Force the window by monkeypatching
  `NotificationFlusher.__init__` to `time.sleep(0.01)` before returning, which makes the defect fail every
  run and leaves the fixed version unaffected.

### CC-05: Double-checked init publishes the flusher before it is initialized
- Severity: Blocker
- Description: `get_flusher()` assigns the module global inside the lock but finishes initialization after
  publishing it (`_flusher = NotificationFlusher.__new__(NotificationFlusher)` then fills fields, or
  `_flusher = NotificationFlusher()` followed by `_flusher.start()` outside the `with _lock` block), so a
  second thread passes the unlocked first check, gets an object whose queue or thread does not exist yet,
  and either raises `AttributeError` in the request path or enqueues into a flusher that never drains.
- Planting: In `app/api/deps.py`, `get_flusher`, as the "fix" for CC-04 written by someone who half
  remembers double-checked locking:
  ```python
  if _flusher is None:
      with _flusher_lock:
          if _flusher is None:
              _flusher = NotificationFlusher(_sender)
      _flusher.start()          # outside the lock, and every thread runs it
      _flusher.queue = deque()  # reset after publishing
  ```
  It looks more careful than CC-04, which is what makes it a good plant.
- Hidden test: Reset the global, monkeypatch `NotificationFlusher.start` to sleep 5 ms and set a flag,
  then hammer `get_flusher` from 8 barrier released threads and assert `start` was called exactly once and
  that every caller observed a fully built object (assert on each returned value that the queue attribute
  exists and is the same object). The defect either raises in a worker thread, which the test surfaces by
  reading each future's result, or records more than one `start`.

### CC-06: Rate limiter counter increments are read-modify-write on a bare dict
- Severity: Major
- Description: The `rate_limit` dependency does `_hits[key] = _hits.get(key, 0) + 1` and compares against
  the limit with no lock, so concurrent requests for the same API key read the same value and write back
  the same increment; the counter undercounts under exactly the load the limiter exists for, and the
  periodic sweep that iterates `_hits` while another thread inserts raises `RuntimeError: dictionary
  changed size during iteration` inside a request.
- Planting: In `app/api/deps.py`, a new module level `_hits: dict[str, int] = {}` next to `_sender` and a
  `rate_limit(principal: CurrentPrincipal)` dependency added to the routers in
  `app/api/routers/orders.py`. Feature: "cap each API key at 100 writes per minute". The author reasons
  that dict operations are atomic, which is true for a single `__setitem__` but not for the get, add, and
  set as a group.
- Hidden test: Call the limiter's counting function 500 times from a `ThreadPoolExecutor(max_workers=8)`
  with a `threading.Barrier` in front of each call, for one key, with the limit set high enough that
  nothing trips. Assert the final count equals 500. Separately, run the sweep in one thread while another
  inserts new keys and assert no exception escapes. The defect loses increments and, in the second part,
  raises `RuntimeError`.

### CC-07: The lock is a new object on every call, so it locks nothing
- Severity: Major
- Description: The guarded section is written as `with threading.Lock():` (or `with Lock() as _:`), which
  constructs a fresh uncontended lock per call, so two threads never exclude each other; the code reads as
  correctly synchronized and the reviewer's eye slides over it because the `with` statement is there.
- Planting: In `app/services/reservations.py`, `ReservationCache.release` or the compaction helper that
  drops expired holds. Feature: the same stock reservation cache as CC-01. Elsewhere in the class
  `self._lock` is used correctly, so the one place that writes `with threading.Lock():` looks consistent
  at a glance. A `# guard the reservation map` comment above it sells the mistake.
- Hidden test: Monkeypatch `threading.Lock` in the module under test with a factory that counts
  constructions and records acquisitions per instance. Call the method 50 times from a threadpool and
  assert the factory was constructed once for the whole cache, not once per call. Also run the arithmetic
  check from CC-01's test shape (barrier released concurrent releases, assert the final held total is
  correct) so a fix that changes the lock but not the logic still passes only when both hold.

### CC-08: A ThreadPoolExecutor is created per call and never shut down
- Severity: Major
- Description: `NotificationService.send_many` (or the flusher's batch dispatch) builds
  `ThreadPoolExecutor(max_workers=8)` inside the function on every call without a `with` block or
  `shutdown()`, so each batch leaks up to eight threads that live until the process exits; under sustained
  traffic the process accumulates thousands of threads and eventually fails with `RuntimeError: can't
  start new thread`.
- Planting: In `app/services/notification.py`, new `send_many(messages: list[Message]) -> None`. Feature:
  "send the daily digest in parallel". The mistaken code is `pool = ThreadPoolExecutor(max_workers=8)` at
  the top of the method, `pool.submit(self._deliver, m)` in a loop, and a `return` with no shutdown. The
  correct shape is one executor owned by the flusher, or a `with ThreadPoolExecutor(...) as pool:` block.
- Hidden test: Record `threading.active_count()` before, call `send_many` with 20 messages ten times over,
  wait for the sender to have recorded all 200 messages, then assert `threading.active_count()` is back
  within 2 of the baseline. Give the assertion a short bounded retry loop (up to 2 seconds) so a correctly
  shut down pool that is still winding down does not flake. The defect stays 80 threads above the
  baseline.

### CC-09: Futures are submitted and their exceptions are never read
- Severity: Major
- Description: The batch dispatch does `for m in batch: pool.submit(self._deliver, m)` and never collects
  the futures, so a `RetryExhausted` or a `TypeError` inside `_deliver` is stored on a future nobody
  inspects and vanishes: the flusher reports success, `WorkerStats` style counters are never incremented,
  and dropped customer emails are invisible in the logs.
- Planting: In `app/services/notification.py`, the flusher's `_flush_batch`. Feature: the batching
  flusher. The author writes the submit loop and moves on because the return value looked useless. A
  variant that is equally plantable: the futures are collected into a list and the code calls
  `concurrent.futures.wait(futures)` but never `future.result()` or `future.exception()`.
- Hidden test: Use a `Sender` stub whose `send` raises `ConnectionError` for exactly one dedupe key and
  records the rest. Call the flush path with four messages and assert that the failure is observable:
  either the call raises, or the flusher's error list contains that dedupe key. Assert the three good
  messages still went out, so a fix that aborts the whole batch on the first failure also fails. The
  defect leaves the error list empty and returns cleanly.

### CC-10: The flusher is a daemon thread, so queued notifications die at shutdown
- Severity: Major
- Description: `NotificationFlusher.start` uses `threading.Thread(..., daemon=True)` and nothing joins it,
  so on every deploy or `uvicorn` restart the interpreter exits while the queue still holds messages;
  confirmation and cancellation emails for orders that were already committed to the database are silently
  lost, and the customer sees a paid order with no email.
- Planting: In `app/services/notification.py`, `NotificationFlusher.start`, plus a `create_app` lifespan
  in `app/api/main.py` that starts the flusher and has no shutdown branch. Feature: the batching flusher.
  `daemon=True` reads as the responsible choice ("do not block interpreter exit") and is the single most
  common way this is written wrong.
- Hidden test: Enqueue 10 messages with the flusher paused (monkeypatch its sleep or hold its start
  event), then call the shutdown path the app's lifespan calls and assert all 10 dedupe keys are present
  in the sender after it returns. Assert the flusher thread is not alive afterwards, joined with
  `timeout=2`. The defect returns immediately with messages still queued.

### CC-11: Non-atomic in-place rewrite of the reservation snapshot
- Severity: Major
- Description: The snapshot writer opens the file with `open(path, "w")`, which truncates first, then
  serializes the reservation map, so a crash, a signal, or an exception raised by the serializer between
  truncate and write leaves a zero length or half written file, and the next start reads a corrupt
  snapshot and loses every outstanding hold. Two threads writing at once interleave into the same file.
- Planting: New `app/services/reservations.py`, `ReservationCache.persist`, called from the expiry thread
  every 30 seconds and from the app lifespan. Feature: "survive a restart without dropping reservations".
  The mistaken code is `with open(self.path, "w") as fh: json.dump(self._held, fh)`. The fix is write to
  `path.with_suffix(".tmp")` in the same directory, then `os.replace`, and to hold `self._lock` for the
  snapshot copy.
- Hidden test: Write a good snapshot, then call `persist` with `json.dump` monkeypatched to raise halfway
  through (a serializer stub that raises after emitting some bytes), catching the error. Assert the
  original file still parses and still holds the original contents. Second part: call `persist` from 4
  threads with distinct maps and assert the file parses as valid JSON every time. The defect leaves an
  unparseable file in both parts.

### CC-12: Metrics counter increments lose updates under the threadpool
- Severity: Minor
- Description: `OrderService.create` bumps a module level `_created_total += 1` (via `global`) or
  `_metrics["orders_created"] += 1` with no lock, so concurrent requests lose increments and the number
  reported by `/reports/metrics` drifts below the real count; nothing user facing breaks, but the
  dashboard that capacity decisions rest on is quietly wrong.
- Planting: In `app/services/order_service.py` module scope plus `OrderService.create`, exposed through a
  new counter field in `app/api/routers/reports.py`. Feature: "expose an orders created counter for the
  ops dashboard". A `# CPython increments are cheap` comment is a realistic touch.
- Hidden test: Reset the counter, then call the counting helper 1000 times from a
  `ThreadPoolExecutor(max_workers=8)` with each call gated behind a shared `threading.Barrier` in batches,
  and assert the final value equals 1000. Use the raw counting function rather than the full `create` path
  so the test stays fast and needs no database. The defect lands a few counts short on nearly every run;
  running the loop 5 times and asserting all 5 totals makes it deterministic in practice.

### CC-13: Expiry thread polls with time.sleep instead of waiting on an Event
- Severity: Minor
- Description: The reservation expiry loop is `while not self._stopped: time.sleep(30); self._expire()`,
  so `stop()` does not wake it and shutdown blocks for up to 30 seconds, tests that exercise the loop have
  to sleep, and expiry is coarse: a hold released at second 1 keeps blocking stock until second 30. A
  `threading.Event` with `wait(timeout=30)` is both the fast path and the clean shutdown.
- Planting: In `app/services/reservations.py`, `ReservationCache._run`. Feature: the background expiry
  thread from curriculum exercise 24. The author already has a `self._stopped = False` flag and reaches
  for `time.sleep` out of habit.
- Hidden test: Start the loop with a 30 second period, call `stop()` immediately, and join with
  `timeout=1`. Assert the thread is not alive. The fix (an `Event.wait`) returns in milliseconds; the
  defect fails the join. Keep the period long so the test cannot pass by accident.

### CC-14: A threading.Timer is created per notification and never cancelled
- Severity: Minor
- Description: `NotificationFlusher.enqueue` schedules `threading.Timer(2.0, self.flush).start()` on every
  message so a partial batch is not stranded, but the timers are neither cancelled when a full batch
  flushes nor deduplicated, so a burst of 500 messages leaves 500 pending timer threads that all fire and
  all call `flush`, multiplying lock contention and thread count for no benefit.
- Planting: In `app/services/notification.py`, `NotificationFlusher.enqueue`. Feature: "flush a partial
  batch after 2 seconds so a quiet queue is not stuck". The single timer call is one line and reads as
  obviously correct. The fix keeps one timer handle, cancels it on flush, or drops timers entirely in
  favor of the existing loop's `Event.wait(timeout=...)`.
- Hidden test: Enqueue 100 messages, then assert the number of live `threading.Timer` threads (count
  `threading.enumerate()` entries whose class is `threading.Timer`) is at most 1. The defect leaves close
  to 100. Cancel any survivors in a fixture teardown so a failing run does not slow the rest of the suite.

### CC-15: lru_cache on a method with a mutable argument
- Severity: Minor
- Description: `@lru_cache(maxsize=256)` is put on `OrderService.quote_for` or
  `ReservationCache.available_many`, whose arguments are `self` and a list of SKUs. Every distinct `self`
  is a cache key, so the cache pins one `OrderService` and its `Session` per request forever, and a list
  argument raises `TypeError: unhashable type: 'list'` the first time a caller passes one. The cache is
  also shared across threads holding results computed against a different request's session, so one
  customer's quote can be served to another.
- Planting: In `app/services/order_service.py`. Feature: "avoid recomputing the same quote when the
  checkout page polls". The author copies the `@lru_cache(maxsize=1)` pattern from `get_settings` in
  `app/services/config.py` and `get_engine` in `app/db/session.py` without noticing that those decorate
  module level zero argument functions.
- Hidden test: Two parts. First, call the method with a list argument and assert it does not raise
  `TypeError` (the fix takes a tuple or drops the cache). Second, build two `OrderService` instances on
  two different Sessions, call the method with the same SKUs on both, and assert the second result
  reflects the second session's data, not the first's. Also assert with `gc` and a `weakref` that the
  first service is collectable after its session closes; the defect keeps it alive in the cache.

### CC-16: Worker and flusher threads are created without a name
- Severity: Nit
- Description: `threading.Thread(target=self._run)` is started with no `name=`, so during an incident
  every log line and every `faulthandler` dump shows `Thread-7 (_run)` and nobody can tell the flusher
  from the expiry thread from the threadpool.
- Planting: In `app/services/notification.py` and `app/services/reservations.py`, wherever the feature
  starts a thread. Nothing else in the codebase starts threads, so there is no local convention to copy
  and this is a genuine oversight rather than a rule violation.
- Hidden test: Start both threads and assert their names are stable, human readable strings (for example
  that each `t.name` does not match `^Thread-\d`). Keep the assertion loose about the exact wording so the
  fix is not pinned to one string.

### CC-17: Comment asserts an operation is atomic when it is not
- Severity: Nit
- Description: A `# atomic, no lock needed` comment sits above a compound operation such as `_hits[key] =
  _hits.get(key, 0) + 1` or `self.pending = self.pending + batch`, teaching the next reader the wrong rule
  even after the arithmetic is fixed elsewhere.
- Planting: In `app/api/deps.py` above the rate limiter counter, or in `app/services/notification.py`
  above the batch swap. Feature: any of the three. The comment is exactly the sort of thing added in
  review to preempt a question.
- Hidden test: Read the changed files as text with `Path(...).read_text()` and assert no line contains
  both a comment marker and the word "atomic" next to an assignment that reads its own target. A simpler
  stable form: assert the string `"atomic, no lock needed"` does not appear in `app/api/deps.py` or
  `app/services/notification.py`.

### CC-18: Leftover threading import and an unused lock attribute
- Severity: Nit
- Description: An earlier draft of the feature used a `threading.RLock` that the final version does not,
  and the import and the `self._rlock = threading.RLock()` line survive; ruff `F401` fails on the import
  and the dead attribute suggests to the next reader that the class is guarded when it is not.
- Planting: In `app/services/reservations.py` or `app/api/deps.py`, module imports plus `__init__`. Any of
  the three features reworked mid PR. This mirrors `SV-21` in the services catalog and is the standard
  CI-red nit.
- Hidden test: `subprocess.run(["uv", "run", "ruff", "check", "--select", "F401",
  "app/services/reservations.py", "app/api/deps.py", "app/services/notification.py"])` from the repository
  root and assert `returncode == 0`. Also assert the class exposes exactly one lock attribute so the dead
  one is really gone.

## Looks wrong but is fine

### CC-CLEAN-01: `sent.append` from several threads with no lock
- Pattern: `InMemorySender.send` in `app/services/notification.py` does `self.sent.append(message)` on a
  plain list, and the process wide `_sender = InMemorySender()` in `app/api/deps.py` is shared by every
  request thread and by the new flusher's threadpool.
- Why it is fine: `list.append` is a single bytecode that runs under the GIL and is atomic in CPython;
  concurrent appends cannot lose a message or corrupt the list. Nothing in the codebase does a
  read-modify-write on `sent` (no `sent = sent + [m]`, no length check followed by an append), and its
  only readers are tests that run after the writers have joined. The `fail_times` counter next to it is
  only ever set in single threaded tests.
- What a reviewer might wrongly say: "Appending to a shared list from multiple threads is a race, wrap
  `sent` in a `threading.Lock` or use a `queue.Queue`."

### CC-CLEAN-02: `lru_cache` on `get_settings` with no lock
- Pattern: `@lru_cache(maxsize=1) def get_settings() -> Settings` in `app/services/config.py`, and the
  same shape on `get_engine` and `get_session_factory` in `app/db/session.py`, all called from every
  request thread with no synchronization and no double-checked init.
- Why it is fine: `lru_cache` does not hold a lock across the wrapped call, so under a cold cache two
  threads can both run `load_settings()`, but `load_settings` is pure, reads only `os.environ`, and
  returns a frozen dataclass; the loser's `Settings` is simply discarded and every caller ends up with an
  equivalent value. The cache dict update itself is atomic under the GIL, so no caller ever sees a partial
  entry. The whole cache is also replaced once at startup and cleared once per session by `_settings_env`
  in `conftest.py`, never mutated at runtime. `get_engine` is the same story: SQLAlchemy `Engine` and
  `sessionmaker` are documented as thread safe, and a duplicate engine built during a cold start is
  garbage collected.
- What a reviewer might wrongly say: "`lru_cache` is not thread safe, two threads can both build the
  engine and we end up with two connection pools, this needs double-checked locking" or "settings should
  be initialized eagerly under a lock."

### CC-CLEAN-03: One module level session factory shared by every request thread
- Pattern: `get_session_factory()` in `app/db/session.py` is a process wide `sessionmaker` cached with
  `lru_cache`, and `get_db` in `app/api/deps.py` calls it on every request from a threadpool thread.
- Why it is fine: what is shared is the factory and the engine, not a `Session`. `get_db` calls the
  factory to build a fresh `Session` per request and closes it in `finally`, so no `Session` and no
  `Connection` ever crosses a thread; SQLAlchemy's own guidance is exactly one engine per process and one
  Session per unit of work. Sharing the `Session` instead is defect CC-03.
- What a reviewer might wrongly say: "The session factory is a global mutable singleton shared across
  request threads, this needs `scoped_session` or a thread local" (`scoped_session` would work but changes
  nothing here, and asserting the current code is broken is the false positive).

### CC-CLEAN-04: `time.sleep` backoff inside a sync handler on the threadpool
- Pattern: `NotificationService._deliver` calls `retry(...)` from `app/services/retry.py`, whose default
  `sleep` is `time.sleep`, and it runs inside a sync FastAPI handler or inside a handler dispatched by
  `QueueWorker._invoke`.
- Why it is fine: blocking is the point of a worker thread. FastAPI runs `def` handlers on a threadpool
  and `QueueWorker._invoke` sends every non-coroutine handler through `asyncio.to_thread`, so the sleep
  parks one pool thread and never the event loop, which is what convention 5 requires. `_deliver` in fact
  passes `sleep=lambda _s: None`, so the production path does not even sleep, and the same reasoning is
  recorded as `AS-CLEAN-04` in the async catalog.
- What a reviewer might wrongly say: "`time.sleep` in a request path blocks the server, this must be
  `await asyncio.sleep`" or "the retry backoff will stall every other request."
