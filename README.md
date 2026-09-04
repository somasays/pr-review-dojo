# pr-review-dojo

A small order-management system used as the base for code review and rewrite
interview practice. Exercises are pull requests against this codebase. See
`CLAUDE.md` for how grading works and `exercises/` for the catalog.

## Layout

| Package | What it does |
| --- | --- |
| `app/domain` | Pure logic with no IO: `Money` (Decimal-backed), pricing rules with discounts and tax, `DateRange` utilities, and the order status state machine. Type-checked with mypy strict. |
| `app/db` | SQLAlchemy 2.x models (`Customer`, `Product`, `Order`, `OrderItem`), the engine and session factory, repositories that own all queries, and Alembic migrations. |
| `app/services` | `OrderService` (create, pay, ship, cancel), `PricingService` (adapts products and discount codes to the domain), `NotificationService` (retried sends with dedupe keys), a `retry` helper, and the settings loader. |
| `app/api` | FastAPI app. Routers for orders, customers, and reports. API-key auth via `X-API-Key`, admin keys from settings, customer keys hashed in the database. Pydantic response models are explicit allowlists. |
| `app/jobs` | PySpark. `daily_orders` is a batch job that aggregates one day of orders per customer. `order_events_stream` is a Structured Streaming job that upserts the latest status per order. Both run on `local[*]` with small fixtures. |
| `app/async_tasks` | An asyncio worker that drains a queue and dispatches tasks to service handlers in a thread, with bounded concurrency and retries. |
| `tests` | pytest suite. SQLite in memory for database tests, `chispa` for DataFrame assertions, a session-scoped SparkSession. |

## Data layout

The data lake root (`DATA_LAKE_ROOT`) holds parquet tables, each partitioned
by `dt` (a `YYYY-MM-DD` string, always a string, never inferred as a date):

```
<root>/orders/dt=2026-08-01/...
<root>/daily_customer_orders/dt=2026-08-01/...
```

The streaming job reads newline-delimited JSON events from a directory (a
stand-in for the Kafka topic) and writes `orders_latest`, keyed by `order_id`.
Every `foreachBatch` write is a full merge, so replaying a batch is safe.

## Conventions

These are the rules of this codebase. Exercise PRs will break them.

1. **No f-string or `%`-formatted SQL.** Every query goes through SQLAlchemy
   Core or ORM constructs with bound parameters. Raw SQL uses `text()` with
   named parameters.
2. **Decimal for money.** `Money` wraps a quantized `Decimal`. Floats are
   rejected at construction. Database columns are `Numeric(12, 2)`.
3. **All writes are idempotent.** Order creation is keyed by
   `(customer_id, idempotency_key)`. Status changes are no-ops when already in
   the target state. Notifications carry a `dedupe_key`. Batch jobs overwrite
   only the partitions they compute. Stream merges are replay-safe.
4. **Partition filters on every Spark read.** `read_orders` takes a
   `DateRange` and filters on `dt` before anything else. No full-table scans.
5. **No blocking calls in async code.** Sync handlers run through
   `asyncio.to_thread`. Never `time.sleep`, `requests`, or a sync database
   session directly inside a coroutine.
6. **Every public function has a test.** Public means no leading underscore
   and importable from the package.
7. **Timestamps are timezone-aware UTC.** `ensure_utc` rejects naive values.
8. **Repositories never commit.** The caller (request dependency, service
   script, worker) owns the transaction boundary.
9. **Response models are allowlists.** Never return an ORM row through a
   `response_model` that was not written for that endpoint.
10. **Retry only idempotent operations.** `retry()` is for sends and reads
    that can safely repeat.

## Running

```
uv sync
uv run pytest
uv run ruff check . && uv run mypy
uv run alembic upgrade head
uv run uvicorn app.api.main:app --reload
uv run python -m app.jobs.daily_orders --root ./data --start 2026-08-01 --end 2026-08-01
```

Spark needs a JDK 17 on `JAVA_HOME`. On macOS with Homebrew, `conftest.py`
picks up `/opt/homebrew/opt/openjdk@17` automatically.

## Settings

All settings are read once from the environment by `app.services.config`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_ENV` | `dev` | `prod` forbids SQLite |
| `DATABASE_URL` | `sqlite:///./dojo.db` | SQLAlchemy URL |
| `ADMIN_API_KEYS` | empty | comma-separated admin keys |
| `NOTIFY_RETRIES` | `3` | send attempts |
| `WORKER_CONCURRENCY` | `4` | async worker bound |
| `DATA_LAKE_ROOT` | `./data` | parquet root |
| `PAGE_SIZE_MAX` | `200` | list endpoint cap |
| `RATE_LIMIT_PER_MINUTE` | `100` | writes allowed per API key per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | length of the rate limit window |
