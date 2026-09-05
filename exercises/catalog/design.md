# Design catalog

Structural findings planted alongside defects in every exercise, in any
domain. A design finding is code that works today and would still get a
comment from a strong reviewer: it sits in the wrong layer, does too much,
depends in the wrong direction, duplicates something that exists, cannot be
tested without the world, or adds structure nobody asked for. Each entry
names how to plant it in this codebase and what a structural hidden test
looks like (import graph, `ast`, or `inspect` based, fast, no IO). Severity
follows `CLAUDE.md`: Major when the next feature or the next test will pay
for it, Minor when it is a maintainability cost. The `refactor` kind uses
these same entries, phrased as an opportunity rather than a request. The
last section lists structure that looks like over-engineering but is fine.

## Findings

### DS-01: Handler does the whole job
- Severity: Major
- Description: A router handler parses input, prices, writes rows, and sends notifications inline instead of calling the service layer.
- Planting: A new endpoint in `app/api/routers/orders.py` (reorder, bulk cancel, refund) builds `OrderItem` rows, decrements stock, and calls `NotificationService` directly, with `OrderService` untouched. Reads as "it was quicker to do it here".
- Hidden test: `ast` walk of the handler: no attribute access named `add`, `flush`, or `order_confirmed` inside the handler body, and `OrderService` is referenced.

### DS-02: Service owns config, IO, and formatting
- Severity: Major
- Description: One service class reads settings, calls an HTTP gateway, writes rows, and formats the customer-facing text.
- Planting: A new `PaymentService` or `ExportService` in `app/services/` has `_load_settings`, `_post`, `_write`, and `_format_receipt` methods on one class and a 60-line `run` that calls them in order.
- Hidden test: the module exposes a pure function for the formatting step (importable, takes plain values, returns a string) and the class has at most one IO collaborator injected through `__init__`.

### DS-03: String dispatch chain
- Severity: Minor
- Description: An `if kind == "x": ... elif kind == "y":` ladder over a string selects behavior that a table or the existing enum would express.
- Planting: In `app/domain/pricing.py` or `app/services/pricing_service.py`, a new promotion type is added by extending a five-branch chain on a raw string instead of `DiscountKind` and a dict of handlers.
- Hidden test: `ast` count of `If` nodes whose test compares a name to a string constant inside the function is at most 1.

### DS-04: Concrete dependency where a protocol exists
- Severity: Major
- Description: New code depends on `InMemorySender` or a concrete gateway class although `Sender` (a `Protocol`) already exists for exactly this seam.
- Planting: A new dispatcher or flusher annotates and constructs `InMemorySender` directly, or a new service takes `smtp_host` and builds its own client, so tests cannot substitute a fake.
- Hidden test: `inspect.signature` of the constructor has a parameter annotated `Sender`, and the module does not import `InMemorySender`.

### DS-05: Router builds queries
- Severity: Major
- Description: A handler imports `select` and composes SQLAlchemy queries instead of calling a repository method.
- Planting: A new list or report endpoint writes `select(Order).where(...)` in `app/api/routers/`, while `OrderRepository` has, or could have, the method. Reads as "the repository did not have the filter I needed".
- Hidden test: the router module has no import from `sqlalchemy` and no attribute named `execute` or `scalars` on a session.

### DS-06: Domain imports outward
- Severity: Major
- Description: A module under `app/domain/` imports `app.db`, `app.services`, or `app.api`, breaking the pure-logic layer.
- Planting: A new domain helper takes a `Product` ORM row or reads `get_settings()` for a tax rate.
- Hidden test: walk `app/domain/*.py` with `ast` and assert no `Import` or `ImportFrom` names `app.db`, `app.services`, or `app.api`.

### DS-07: ORM rows cross the boundary
- Severity: Major
- Description: ORM instances are handed to a background task, a Spark job, a worker payload, or a formatter that runs after the session is gone.
- Planting: A handler enqueues `Task("notify", {"order": order})` with the `Order` instance, or an export function receives `list[Order]` and reads `order.customer.email` lazily.
- Hidden test: the enqueued payload contains only ids and plain values (`json.dumps` succeeds), or the export function's parameter is annotated with a dataclass or `Sequence[tuple]`.

### DS-08: Re-implements an existing helper
- Severity: Minor
- Description: New code rewrites `Money.allocate`, `Money.percent`, `DateRange.split`, `retry`, `ensure_utc`, or an existing repository method by hand.
- Planting: A "spread discount across lines" loop with `//` and a remainder, a hand-rolled retry `for attempt in range(3)`, or a `while cur <= end` day loop next to `DateRange`.
- Hidden test: `ast` walk of the new module finds a call to the existing helper by name and no local function with the duplicated shape.

### DS-09: Clock inside pure logic
- Severity: Minor
- Description: A domain or service function calls `utcnow()` or `datetime.now()` internally, so tests cannot pin time and results change by the hour.
- Planting: A new `is_expired`, `fiscal_quarter`, or `late_fee` function reads the clock instead of taking `now` or `today` as a parameter.
- Hidden test: `inspect.signature` has a `now` or `today` parameter, and calling it with a fixed value gives a deterministic answer.

### DS-10: Settings read deep inside
- Severity: Minor
- Description: `get_settings()` is called inside a method body instead of the value being injected at construction, hiding the dependency and making the method untestable without environment variables.
- Planting: A new service method calls `get_settings().page_size_max` or `get_settings().smtp_host` in the middle of its logic although the class already receives `Settings`.
- Hidden test: the module calls `get_settings` at most once and only in `__init__` or a factory, checked with `ast`.

### DS-11: Boolean flag parameter
- Severity: Minor
- Description: A function takes `dry_run`, `notify`, or `admin` booleans that switch it between two behaviors, so every caller reads like `run(order, True, False)`.
- Planting: A new `cancel(order_id, restock=True, notify=True, force=False)` or a Spark `run(..., backfill=True)` with branches on each flag.
- Hidden test: `inspect.signature` has no parameter annotated `bool` (or at most one), and the two behaviors are separate functions.

### DS-12: Misleading name
- Severity: Minor
- Description: The name promises one thing and the body does another: `validate_` mutates, `get_` creates, `total` excludes tax, `is_` returns a list.
- Planting: A new `get_or_create_address` named `get_address`, or `calculate_total` that also writes the order.
- Hidden test: none; graded from the review comment. The reference fix renames or splits the function.

### DS-13: Primitive obsession
- Severity: Minor
- Description: Five related strings and numbers travel together as positional arguments where `Money`, `DateRange`, `Line`, or a small dataclass exists.
- Planting: A new function signature like `(amount: Decimal, currency: str, start: str, end: str, region: str)` when the callers already hold `Money` and `DateRange`.
- Hidden test: `inspect.signature` parameter count is at most 3, or a parameter is annotated with the domain type.

### DS-14: Stringly-typed status
- Severity: Minor
- Description: New code compares and assigns raw status strings instead of `OrderStatus`, bypassing the transition table's vocabulary.
- Planting: `if order.status in ("paid", "shipped")` and `order.status = "refunded"` in a new service method while `OrderStatus` and `transition` sit one import away.
- Hidden test: `ast` walk finds no string constant that equals an `OrderStatus` value inside the new function.

### DS-15: Abstract base class with one implementation
- Severity: Minor
- Description: A new `ABC` with abstract methods and exactly one subclass, added "so it is easy to swap later".
- Planting: `BaseExporter` plus `CsvExporter`, or `AbstractRateLimiter` plus `MemoryRateLimiter`, in a PR that needs one.
- Hidden test: the module defines no class whose bases include `ABC`, and the concrete class is used directly.

### DS-16: Factory or registry for one product
- Severity: Minor
- Description: A `make_sender(kind: str)` factory, a plugin registry, or a class-level `register` decorator that has one entry.
- Planting: A new notification channel adds `SENDERS: dict[str, type[Sender]]` with one key and a `create_sender` that reads the kind from settings.
- Hidden test: the module has no function whose name starts with `make_`, `create_`, or `build_` returning a class chosen by a string, and no dict of types.

### DS-17: Speculative generality
- Severity: Minor
- Description: A helper is generic over a type parameter, takes callbacks, or accepts options that no caller uses.
- Planting: `def paginate[T](items: Iterable[T], *, key=None, reverse=False, chunk=None, on_page=None)` called once with defaults.
- Hidden test: `inspect.signature` of the helper has at most the parameters the single caller passes.

### DS-18: Feature flag nobody asked for
- Severity: Minor
- Description: A new `Settings` field toggles the feature on and off, doubling the paths to test, with no rollout plan behind it.
- Planting: `enable_volume_discounts: bool = True` added to `Settings` and checked in three places.
- Hidden test: `Settings` has no field whose name starts with `enable_` or `use_`.

### DS-19: Catch and re-wrap at every layer
- Severity: Major
- Description: Each layer catches `Exception`, wraps it in its own error type, and re-raises, so the original cause and stack are three layers away and every caller handles the wrapper.
- Planting: `RepositoryError`, `ServiceError`, and `ApiError` introduced together, each `except Exception as exc: raise XError(str(exc))` without `from exc`.
- Hidden test: raising `IntegrityError` inside the repository surfaces to the service test as `IntegrityError` or a wrapper with `__cause__` set.

### DS-20: Duplicated branch
- Severity: Minor
- Description: Two nearly identical code paths for admin and customer, or batch and backfill, that differ in one predicate.
- Planting: `list_orders_admin` and `list_orders_customer` in a router or repository, twenty lines each, differing only in the `where` clause.
- Hidden test: the two public functions share one private helper (both call it, checked with `ast`), or only one public function remains with the predicate as a parameter.

### DS-21: Orchestration mixed with format details
- Severity: Minor
- Description: A function that sequences steps (load, compute, write, notify) also contains byte-level or string-format details (CSV quoting, email HTML, JSON layout).
- Planting: A new export or digest function interleaves `csv.writer` calls and column formatting with repository reads and the notification send.
- Hidden test: a pure `format_*` or `render_*` function exists in the module and is importable without a session.

### DS-22: Public function without a test
- Severity: Minor
- Description: A new public function in the PR has no test in `tests/`, breaking convention 6 in `README.md`.
- Planting: The feature adds two public helpers and tests one.
- Hidden test: for each new public function name, some file under `tests/` references it.

## Looks over-engineered but is fine

### DS-CLEAN-01: Protocol with one implementation
- Pattern: `Sender` is a `Protocol` and `InMemorySender` is the only implementation in the repo.
- Why it is fine: The protocol is the test seam and the production sender lives outside this repo. A protocol costs four lines and no runtime.
- What a reviewer might wrongly say: "YAGNI, just use InMemorySender directly."

### DS-CLEAN-02: Command dataclass for one call site
- Pattern: `CreateOrderCommand` wraps four fields that `OrderService.create` could take as arguments.
- Why it is fine: The command is built by the API and the worker, is immutable, and keeps the service signature stable when the API adds a field.
- What a reviewer might wrongly say: "Unnecessary ceremony, pass the arguments."

### DS-CLEAN-03: One-line repository method
- Pattern: `by_email` is a single `select` wrapped in a method.
- Why it is fine: Repositories are the only place that builds queries (README convention 8); the one-liner is what keeps the router free of SQLAlchemy.
- What a reviewer might wrongly say: "This wrapper adds nothing, inline the query."

### DS-CLEAN-04: Module-level constant table
- Pattern: `TAX_RATES` and `DISCOUNT_CODES` are dicts in code rather than database tables.
- Why it is fine: They change with a deploy, are covered by tests, and the comment says a later change may move them. A table would add a migration, a cache, and an admin endpoint for no current need.
- What a reviewer might wrongly say: "Hard-coded business data, this belongs in the database."
