# Rewrite smell catalog

A rewrite exercise is a PR that works. Every test passes, ruff and mypy are green,
and there are zero planted defects. What is wrong is the shape: one function that
does five jobs, a chain of `withColumn` calls that nobody can read, a router that
carries its own SQL. The candidate's job is to restructure it so the next engineer
can change it safely, while keeping behavior identical. Defect exercises grade what
you find; rewrite exercises grade what you leave alone. The fix rubric's
Proportionality section is the whole game here: the winning rewrite is the smallest
set of moves that removes the smell, keeps the existing tests untouched or minimally
extended, and adds no abstraction the codebase did not already need. An exercise
author planting one of these smells must keep the smelly version correct: bound
parameters stay bound, exceptions are re-raised, every flag combination produces the
right result. If the smelly version is wrong, it is a defect exercise, not a rewrite.

## Smells

### RW-01: God function
- Smell: One function parses input, validates it, prices it, writes it, and sends the email.
- Where it shows up here: `app/services/order_service.py`, `OrderService.create`. The
  smelly version inlines `PricingService.quote` (discount lookup, line building, tax),
  the stock check, the `Order` and `OrderItem` construction, the `begin_nested` insert,
  and a hand-built confirmation `Message`, in one 120-line body with local variables
  named `sub`, `disc`, `t`, and `tot`.
- Good rewrite: Pull pricing back into `PricingService.quote` so `create` calls it once
  and receives a `Quote`. Move `Order` and `OrderItem` construction into a private
  `_build_order(cmd, customer, products, quote)` helper. Route the notification through
  `NotificationService.order_confirmed`. `CreateOrderCommand`, the idempotency lookup,
  and the `IntegrityError` race handling stay exactly where they are.
  `tests/test_order_service.py` passes unchanged because the public signature and the
  persisted rows are identical.
- Over-engineered rewrite: An `OrderCreationPipeline` with `ParseStep`, `ValidateStep`,
  `PriceStep`, `PersistStep`, and `NotifyStep` classes behind a `Step` protocol, plus a
  `PipelineRunner`. Five steps that run in a fixed order and share one context object is
  a function with sections, and the pipeline adds a new file and an indirection layer for
  zero flexibility anyone asked for.
- Interviewer question: Why did you keep the `IntegrityError` handling inside `create`
  instead of moving it into the repository along with the insert?

### RW-02: God class
- Smell: A service owns config loading, an HTTP client, database access, and string formatting.
- Where it shows up here: `app/services/order_service.py`. The smelly `OrderService`
  reads `NOTIFY_RETRIES` and `ADMIN_API_KEYS` from `os.environ` in `__init__` (bypassing
  `app.services.config.get_settings`), holds an `httpx.Client` for the email gateway,
  builds subject and body strings in `_format_confirmation`, and runs its own retry loop.
  It still behaves correctly; it is just four objects wearing one coat.
- Good rewrite: Inject `Settings` and a `NotificationService` through the constructor,
  which is what `app/api/deps.py:get_order_service` already does for the real class. Move
  the message formatting into `NotificationService`, where `Message` and the retry policy
  already live. Delete the private retry loop in favor of `app.services.retry.retry`.
  The repositories stay as constructor-built fields. `tests/test_order_service.py` and
  `tests/test_notification.py` cover the seam; the `InMemorySender` fixture is enough.
- Over-engineered rewrite: Splitting into `OrderCommandService`, `OrderQueryService`,
  `OrderNotificationCoordinator`, and an `OrderServiceFacade` that composes them, with a
  `ServiceRegistry` for wiring. The codebase has one composition root (`deps.py`) and one
  caller per method; three classes plus a facade is more surface than the whole `services`
  package had before.
- Interviewer question: What would you have done if `NotificationService` did not already
  exist? Would you have created it in this PR?

### RW-03: Deep nesting
- Smell: Five or more levels of `if` and `for` where each level guards the next.
- Where it shows up here: `app/services/pricing_service.py`, `PricingService.quote`. The
  smelly version loops over items, inside that checks the product exists, inside that
  checks stock, inside that loops over discount codes, inside that branches on
  `DiscountKind`, inside that checks `min_subtotal`. The final `return` is indented 24
  spaces. Every branch is correct and `tests/test_pricing_service.py` is green.
- Good rewrite: Invert the guards into early raises: collect unknown SKUs first and raise
  `UnknownSku`, then check stock and raise `InsufficientStock`, then build `Line` objects.
  Discount selection already exists as `app.domain.pricing.best_discount` and
  `Discount.apply`; call them instead of re-deriving the `DiscountKind` branches. The
  result is three flat sections and one call into the domain. Exceptions, messages, and
  return values are unchanged, so the existing tests pin the behavior.
- Over-engineered rewrite: A `Validator` chain with `SkuValidator`, `StockValidator`, and
  `DiscountValidator` classes, each with `validate(ctx) -> list[Error]`, combined by a
  `CompositeValidator`. Three fixed checks that raise on first failure do not need a
  collection of error objects or a composition mechanism.
- Interviewer question: You raise `UnknownSku` before `InsufficientStock`. Was that order
  chosen by you, or preserved from the original, and how would a caller notice a change?

### RW-04: Twelve-step withColumn chain
- Smell: A Spark transform that adds intermediate columns one at a time, casting the same column repeatedly, then drops the helpers.
- Where it shows up here: `app/jobs/daily_orders.py`, `aggregate_daily`. The smelly
  version chains `withColumn("is_paid", ...)`, `withColumn("paid_amt", ...)`,
  `withColumn("paid_amt", col("paid_amt").cast("decimal(12,2)"))`,
  `withColumn("is_cancelled", ...)`, `withColumn("cancelled_int", ...)`, a second cast of
  `total`, a `withColumn("dt_str", col("dt").cast("string"))` on a column that is
  already a string, and so on for twelve steps, then `groupBy`, then `drop` of six helpers.
  Output matches `DAILY_CUSTOMER_SCHEMA` exactly.
- Good rewrite: Build the two conditional expressions (`paid`, `cancelled`) as local
  `Column` values and pass them straight into `agg`, casting once at the aggregate. Drop
  the redundant `dt` cast and the intermediate columns entirely. The function shrinks to
  the shape of the real one: two expressions, one `groupBy`, one `agg`, one `select`.
  `tests/test_daily_orders.py` uses `chispa` to compare the output DataFrame, so it pins
  the schema and the values without any change.
- Over-engineered rewrite: A `ColumnBuilder` fluent class, a `transforms.py` module with
  `add_paid_flag(df)`, `add_cancelled_flag(df)`, `cast_money(df, col)`, each with its own
  unit test, and a `pipe()` helper that applies them in order. Named single-use
  transforms for a three-expression aggregate is more code than the chain it replaces.
- Interviewer question: Why did you keep the cast inside `agg` rather than casting
  `total` once at read time in `read_orders`?

### RW-05: Mixed IO and logic
- Smell: Pure computation interleaved with database reads and writes so no step can be tested without a session.
- Where it shows up here: `app/services/pricing_service.py`, `PricingService.quote`. The
  smelly version takes a `Session`, calls `ProductRepository.by_skus` once per item inside
  the pricing loop, computes a running subtotal, calls `session.flush()` to write a
  `stock` decrement after each line, then looks up each discount code with a query in the
  middle of the tax calculation. Correct, idempotent, and impossible to test without
  SQLite.
- Good rewrite: Read everything up front: one `by_skus` call for all SKUs, one
  `resolve_discounts` call for all codes. Call the pure `app.domain.pricing.quote` on the
  resulting `Line` and `Discount` lists. Do the stock decrement once, after the quote, in
  `OrderService.create` where the real code does it. `PricingService.quote` no longer
  needs a session in its signature, which is how `tests/test_pricing_service.py` already
  calls it, so those tests are the pin.
- Over-engineered rewrite: A `ProductGateway` protocol with `SqlProductGateway` and
  `InMemoryProductGateway`, a `UnitOfWork` abstraction over the session, and a
  `QuoteContext` dataclass that carries both. There is one database and the repositories
  already are the gateway; a second interface layer with one real implementation is what
  Proportionality is written to penalize.
- Interviewer question: After your change the stock decrement moved out of the pricing
  loop. What guarantees it still happens exactly once per order?

### RW-06: Boolean parameters
- Smell: Flags that switch behavior, so callers pass `dry_run=True, notify=False, admin=True` and readers have to trace every branch.
- Where it shows up here: `app/services/order_service.py`, `OrderService.cancel(order_id,
  restock=True, notify=True, admin=False, dry_run=False)`. The router passes
  `admin=principal.is_admin`, a maintenance script passes `dry_run=True, notify=False`,
  and the body has eight `if` statements on the four flags. Every combination is correct
  and tested. Also seen in `app/jobs/daily_orders.py`, `run(..., backfill=False,
  dry_run=False)` where `backfill` changes the partition range and `dry_run` skips the
  write.
- Good rewrite: Move the admin check to the router, where `Principal.is_admin` already
  lives and where the real `cancel_order` endpoint does it. Split `dry_run` off into a
  separate `preview_cancel(order_id) -> Order` that runs the state check and returns
  without writing. Keep `cancel(order_id)` as the one real path with restock and notify
  unconditional, since no production caller turns them off. For the Spark job, keep `run`
  taking a `DateRange` and let the caller build the range for a backfill; delete
  `dry_run` and have `main` decide whether to call `write_daily`. `tests/test_order_service.py`
  and `tests/test_daily_orders.py` need only the flag arguments removed.
- Over-engineered rewrite: A `CancelOptions` dataclass with the same four booleans, a
  `CancelPolicy` enum with `ADMIN_DRY_RUN`, `CUSTOMER_NOTIFY`, and so on, or a
  `CancelStrategy` hierarchy with one class per combination. Renaming four booleans does
  not remove the branching; it hides it in a type.
- Interviewer question: You deleted the `notify=False` path. Who called it, and what do
  they do now?

### RW-07: Duplicated branches
- Smell: Two nearly identical code paths, one for customer and one for admin, or one for batch and one for backfill.
- Where it shows up here: `app/api/routers/orders.py`. The smelly `get_order` and
  `cancel_order` each have a full `if principal.is_admin:` body and a full `else:` body,
  each with its own `try`, its own `OrderRepository` construction, its own `HTTPException`
  mapping, and its own return, differing in exactly one line: `repo.get(order_id)` versus
  `repo.get_for_customer(order_id, principal.customer)`. In `app/jobs/daily_orders.py`,
  `run` and `run_backfill` are 30 lines each and differ only in how the `DateRange` is
  built.
- Good rewrite: Collapse each router handler to one `try` with the single differing line
  inside it, which is the shape of the real file. Merge `run_backfill` into `run` by
  having both callers pass a `DateRange`; `DateRange.last_n_days` and `DateRange.split`
  already exist in `app/domain/dates.py` for the backfill case. `tests/test_api_orders.py`
  covers both principals on both endpoints and `tests/test_daily_orders.py` covers the
  range, so both pin the merge.
- Over-engineered rewrite: An `OrderAccessPolicy` class with `for_principal(principal)`
  returning a `Scope` object with a `fetch(repo, order_id)` method, or a
  `JobMode` enum threaded through every function in the Spark job. One branch on
  `is_admin` in two handlers does not justify a policy object.
- Interviewer question: If a third role appears next quarter, say a support agent who can
  read any order but cancel none, does your merged handler get a third branch or something
  else?

### RW-08: Primitive obsession
- Smell: Five related strings passed together instead of one dataclass.
- Where it shows up here: `app/services/notification.py`. The smelly
  `NotificationService.order_confirmed(email, customer_name, order_id, total, currency,
  dedupe_key)` takes six positional arguments, and `order_shipped` and `order_cancelled`
  take overlapping subsets in a different order. Callers in `order_service.py` build the
  arguments from `order.customer.email`, `str(order.total)`, `order.currency` at each call
  site. Also seen in `app/jobs/daily_orders.py` where `LakePaths` is gone and every
  function takes `root: str` and builds `f"{root}/orders"` itself.
- Good rewrite: Pass the `Order` (the callers already have it) and let the service pull
  email, id, and total from it, which is one signature change and three call-site edits.
  Restore `LakePaths` for the job; it already exists in the real file and is the whole
  fix. `tests/test_notification.py` asserts on the `Message` fields and dedupe key, so it
  pins the output; the `InMemorySender` is unchanged.
- Over-engineered rewrite: A `Recipient` value object, an `OrderSummary` DTO with a
  `from_order` classmethod, a `NotificationPayload` generic, and a `MessageTemplate`
  registry keyed by event name. The service has three methods with fixed subject lines; a
  template registry is an abstraction with one reader.
- Interviewer question: You now pass the ORM `Order` into the notification service. Does
  that create a lazy-load outside the session, and how do you know?

### RW-09: Inline SQL in the router
- Smell: Query text scattered across request handlers instead of living in the repository.
- Where it shows up here: `app/api/routers/orders.py`. The smelly `list_orders` and
  `get_order` each hold a `text("SELECT ... FROM orders WHERE customer_id = :cid ...")`
  with correctly bound named parameters (so convention 1 is not broken and this is not a
  defect), then map rows into `Order` by hand. The same `WHERE customer_id = :cid`
  predicate appears three times with small differences in column lists.
- Good rewrite: Delete the inline queries and call `OrderRepository.list_for_customer`
  and `OrderRepository.get_for_customer`, which already exist with the same semantics.
  If one query has no repository equivalent, add exactly that one method to
  `app/db/repositories.py` using the ORM `select` style the file already uses. Response
  models are unchanged, so `tests/test_api_orders.py` pins the JSON shape and the
  customer-scoping behavior.
- Over-engineered rewrite: A `QueryBuilder` with `.for_customer().with_status().paged()`
  methods, a `Specification` pattern with `AndSpec` and `OrSpec`, or a generic
  `BaseRepository[T]` with `find_by(**kwargs)`. The repository file has three classes and
  a dozen plain methods; a generic query layer replaces readable methods with a mini ORM
  on top of the ORM.
- Interviewer question: Why did you add a repository method instead of keeping the one
  query in the router where it was easiest to read?

### RW-10: Stringly-typed status
- Smell: Status handling by string literal instead of the `OrderStatus` enum and `transition`.
- Where it shows up here: `app/services/order_service.py`. The smelly `mark_paid`,
  `ship`, and `cancel` compare `order.status == "pending_payment"` and assign
  `order.status = "paid"`, with the legal transitions re-derived as a hand-written `if`
  ladder in each method. All the strings are correct and the ladder matches
  `app/domain/order_state.py`, so nothing is wrong at runtime. `app/db/repositories.py`
  `list_by_status(status: str)` follows suit.
- Good rewrite: Replace the literals with `OrderStatus` members, delete the ladders, and
  route every change through one `_move(order, target)` that calls `transition`, which is
  the real file's shape. Type `list_by_status` as `OrderStatus`. The Spark jobs keep
  their string literals in `isin("paid", "shipped", "delivered")` because the lake column
  is a string; at most, use the `StrEnum` values in the `isin` call. `tests/test_order_service.py`
  and `tests/test_order_state.py` pin every transition.
- Over-engineered rewrite: A `StatusTransitionRegistry` with decorators registering
  handlers per `(from, to)` pair, a `Status` class hierarchy replacing the enum, or a
  Spark UDF that imports `order_state` to validate statuses in `daily_orders`. The enum and
  `transition` already are the registry.
- Interviewer question: `daily_orders.py` still uses `"paid"` as a string literal. Why is
  that acceptable there when it was not acceptable in the service?

### RW-11: foreachBatch does everything inline
- Smell: A streaming sink lambda that holds the dedupe window, the merge, the staging write, and the cleanup in one closure.
- Where it shows up here: `app/jobs/order_events_stream.py`, `start`. The smelly version
  passes a 45-line `def _sink(df, bid):` nested inside `start`, closing over `target` and
  `spark`, containing the `Window.partitionBy("order_id")` row-number dedupe (written out
  twice, once for incoming and once for merged), the `os.path.exists` check, the staging
  write, the `shutil.rmtree`, and the log line. Replay is still safe.
- Good rewrite: Lift the closure into a module-level `upsert_batch(batch, batch_id,
  target)` and extract the repeated window dedupe into `latest_per_order(df)`, called
  twice. `start` becomes wiring only. That is the exact shape of the real file, and
  `tests/test_order_events_stream.py` can call `upsert_batch` directly with a static
  DataFrame, which is the reason for the move. The trigger and checkpoint options stay
  untouched.
- Over-engineered rewrite: A `BatchSink` abstract class with `ParquetMergeSink` and a
  hypothetical `DeltaMergeSink`, a `SinkFactory`, and a `MergeStrategy` enum. There is one
  target format in this repo and the docstring already says the production job differs;
  an abstraction over a sink that does not exist here is speculation.
- Interviewer question: `latest_per_order` is called on the union of existing and
  incoming rows. What happens to the `_batch_id` column for a row that came from the
  existing table?

### RW-12: Magic numbers
- Smell: Bare literals whose meaning lives only in the author's head.
- Where it shows up here: `app/jobs/order_events_stream.py` and `app/api/deps.py`. The
  smelly stream job has `.withWatermark("event_time", "10 minutes")`,
  `.option("maxFilesPerTrigger", 10)`, `processingTime="30 seconds"`, and
  `.cast("decimal(14,2)")` inline with no names. The pagination dependency clamps
  `limit` to `200` inline instead of reading `settings.page_size_max`. Each value is the
  right value.
- Good rewrite: Name the stream constants at module level (`WATERMARK`,
  `MAX_FILES_PER_TRIGGER`, `TRIGGER_INTERVAL`) as the real file does for `WATERMARK`, and
  read the page cap from `Settings`, which already has `page_size_max`. Two constants that
  appear once and mean what they say (`decimal(14,2)` for a sum of `decimal(12,2)`) can
  stay inline with a short comment. No test changes; `tests/test_config.py` already
  covers the settings field.
- Over-engineered rewrite: Promoting every literal to an environment variable with a new
  `StreamSettings` dataclass, a `settings.yaml`, or a `constants.py` package shared across
  jobs. Config for a value nobody changes is a knob with no hand on it and a new failure
  mode at startup.
- Interviewer question: Which of these numbers would you actually expect an operator to
  change, and did you make only those configurable?

### RW-13: Comments that narrate instead of code that explains
- Smell: Every line has a comment restating what the line does, and none says why.
- Where it shows up here: `app/services/order_service.py` and
  `app/jobs/order_events_stream.py`. The smelly version has `# loop over the items`,
  `# set the status to paid`, `# flush the session`, `# write to staging`, and a 12-line
  block comment above `create` that describes the steps in prose. Meanwhile the one
  comment that carries real information in the real file, "Stage first: Spark cannot
  overwrite a path it is still reading from", is missing.
- Good rewrite: Delete the narration. Rename the things the comments were compensating
  for: `x` becomes `winner`, `tmp` becomes `staging`, `_do` becomes `_move`. Keep or add
  the three comments that explain a non-obvious constraint (the staging write, the
  idempotency race, the "let the state machine raise" note in `cancel`). Zero behavior
  change, zero test change; this is a pure readability commit.
- Over-engineered rewrite: Replacing every comment with a docstring that says the same
  thing, adding a `docs/` folder with an architecture overview, or wrapping each
  commented block in a one-line helper function so the function name carries the comment.
  Fifteen two-line helpers is a different way of narrating.
- Interviewer question: Show me one comment you kept and one you deleted, and explain what
  separates them.

### RW-14: Catch Exception at every layer
- Smell: Router, service, and repository each wrap their body in `try: ... except Exception: log; raise`.
- Where it shows up here: `app/api/routers/orders.py`, `app/services/order_service.py`,
  and `app/db/repositories.py`. The smelly version has every method in all three layers
  catching `Exception`, logging the traceback, and re-raising, so one failure logs three
  times and the router then catches the re-raised exception a fourth time to map it to
  HTTP. Nothing is swallowed, so behavior is correct; the log is just noisy and the intent
  is buried.
- Good rewrite: Delete the catch-log-reraise blocks in the repository and service
  entirely. Keep the router's targeted `except NotFound`, `except InvalidTransition`, and
  `except (UnknownSku, UnknownDiscountCode)` mappings to status codes, which is what the
  real file has. Let FastAPI's default handler log anything unexpected once. The only
  legitimate broad catch stays in `app.services.retry.retry`, which needs it to decide
  whether to retry. `tests/test_api_orders.py` asserts status codes, so the HTTP mapping
  is pinned.
- Over-engineered rewrite: An `ErrorTranslator` middleware with a registry mapping
  exception classes to status codes, a custom `AppError` base class with `http_status`
  and `error_code` attributes retrofitted onto every domain exception, and a
  `@handles_errors` decorator on every handler. Five handlers with two or three `except`
  clauses each do not need a framework.
- Interviewer question: What is the one place in this codebase where catching a broad
  `Exception` is correct, and why?

### RW-15: The 80-line Spark transform
- Smell: One `transform(spark, root, day)` that reads, filters, casts, aggregates, selects, and writes.
- Where it shows up here: `app/jobs/daily_orders.py`. The smelly version has a single
  `transform` that builds the parquet path from `root`, reads with the schema, applies
  the `dt` filter (correctly, so convention 4 holds), does the aggregation, renames
  columns twice, and writes with dynamic partition overwrite, then `main` calls it. There
  is no function boundary to test the aggregation on an in-memory DataFrame.
- Good rewrite: Cut along the IO seams: `read_orders(spark, paths, days)`,
  `aggregate_daily(orders)`, `write_daily(df, paths)`, and a `run` that composes them.
  The middle function is pure DataFrame-in, DataFrame-out, which is what
  `tests/test_daily_orders.py` wants to call with a fixture from `app/jobs/fixtures.py`.
  The read filter and the partition-overwrite write move without modification.
- Over-engineered rewrite: A `Job` base class with `extract`, `transform`, `load`
  template methods, a `JobRunner`, and a `Source` and `Sink` protocol pair, all so that two
  jobs in the repo can share a skeleton they do not actually share (one is batch, one is
  streaming). Three functions and a `run` is the whole framework this job needs.
- Interviewer question: Why three functions and not five? What would push you to split
  `aggregate_daily` further?

### RW-16: Copy-pasted repository queries
- Smell: The same `select(Order).where(...)` skeleton repeated with one predicate changed, each with its own pagination and ordering.
- Where it shows up here: `app/db/repositories.py`, `OrderRepository`. The smelly version
  has `list_for_customer`, `list_for_customer_by_status`, `list_paid_for_customer`, and
  `list_recent_for_customer`, each 8 lines, each repeating `.where(Order.customer_id ==
  customer_id).order_by(Order.created_at.desc()).limit(limit).offset(offset)`. The router
  picks one based on which query parameters are present.
- Good rewrite: One `list_for_customer(customer_id, *, status: OrderStatus | None = None,
  since: datetime | None = None, limit, offset)` that appends `where` clauses when the
  optional arguments are given. Four call sites collapse to one, the ordering and paging
  live in one place, and `tests/test_repositories.py` extends by two cases for the optional
  filters. The router's parameter parsing is untouched.
- Over-engineered rewrite: A `Filter` dataclass hierarchy, an `OrderQuery` builder object,
  or a generic `paginate(stmt, page)` helper used by exactly this one repository. Two
  optional keyword arguments on one method is the proportional fix; a builder is the
  disproportionate one.
- Interviewer question: Optional keyword filters can grow without bound. At what count do
  you stop adding arguments, and what do you do instead?

## What the grader looks for in a rewrite

- Behavior is identical: the same inputs produce the same rows, the same status codes,
  the same messages, the same exceptions in the same order. The candidate can say how they
  know (the existing suite, a before-and-after diff of outputs, or both).
- Existing tests pass without modification, or with only mechanical changes (a removed
  flag argument, an import path). A rewrite that has to rewrite the tests to pass is
  suspicious.
- New tests, if any, cover the seam the rewrite created (the now-pure function, the
  extracted `upsert_batch`) and nothing else.
- Names improved: `x`, `tmp`, `_do`, and `data` became `winner`, `staging`, `_move`, and
  `orders`. Renames are confined to the code the rewrite touches.
- IO moved to the edges: reads happen first, pure computation in the middle, writes last,
  and the middle can be called from a test with no session or SparkSession.
- No new dependencies, no new packages, no new top-level modules unless the smell was
  literally that the right module did not exist.
- Existing helpers were reused: `LakePaths`, `DateRange`, `OrderStatus`, `transition`,
  `best_discount`, `retry`, `Message`. Re-implementing one of these is a Proportionality
  deduction even if the reimplementation is fine.
- The PR description names what was deliberately left alone and why. "I did not touch
  `refund` because it was not part of the smell" earns points; silence loses them.
- Over-engineering signs, each a deduction: an interface or protocol with one
  implementation, a factory for one product, a strategy pattern for two cases, a config
  entry for a constant, a base class for two things that do not share behavior.
- More over-engineering signs: a generic helper used once, a new file whose only content
  is moved code plus a class wrapper, a decorator that replaces one `try` block, and a
  "for later" hook nobody asked for.
- The diff is smaller than or comparable to the code it replaces. A rewrite that doubles
  the line count almost always added structure rather than removing it.
- ruff and mypy stay green, and the candidate ran them rather than assuming.

## Commit discipline for rewrites

- One smell per commit, and each commit leaves the suite green. A reviewer should be able
  to check out any commit in the sequence and run `uv run pytest` successfully.
- Separate the moves from the changes. Commit "extract `upsert_batch` from `start`" as a
  pure cut-and-paste with no edits inside the moved code, then commit the renames and
  simplifications in a second step so the diff of each is readable on its own.
- Rename in its own commit. A commit that renames `x` to `winner` in twenty places and
  also changes logic hides the logic change; keep the mechanical commit mechanical.
- Add or extend a test in the same commit as the seam it covers, not in a final "add
  tests" commit. The test proves the extracted function works in isolation at the moment
  it becomes isolated.
- Write the PR description last and make it match the commits: what changed, in order,
  what was left alone on purpose, and what the next PR should take on. The grader reads
  it before the diff.
