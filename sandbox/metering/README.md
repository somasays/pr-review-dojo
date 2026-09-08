# metering

A utility metering and billing service: a meter reports periodic readings,
a tariff turns the consumption between two readings into a charge, and a
billing period collects one account's meters into a bill. This is a sixth,
self-contained codebase used as a base for a different set of practice
exercises than `app/`, `sandbox/rooms/`, `sandbox/lockers/`,
`sandbox/library/`, and `sandbox/expenses/`. It does not import anything
from any of them.

## Vocabulary

- **Account**: a person or business billed for consumption. `Account.active`
  is the only restriction on who may submit readings or receive bills.
- **Meter**: one physical meter on an account, identified by its `serial`.
  `max_reading` is the value it rolls over at, back to zero.
- **Reading**: one entry in the append-only reading ledger: a meter's value
  at a point in time. See convention 2 below; there is no such thing as
  "updating" a reading.
- **Tariff**: the pricing rule applied to a period's consumption: a standing
  charge per day plus a set of bands (`domain.tariff.Tariff`,
  `domain.tariff.Band`).
- **Band**: one slice of a tariff's rate schedule, a rate per kWh that
  applies up to a cumulative kWh threshold, or without limit for the last
  band.
- **Billing period**: the half-open date range `[start, end)` a bill covers.
- **Bill**: the amount owed for one account's consumption over one billing
  period, generated once and re-fetched, never regenerated, on repeat
  requests for the same period.

## Layout

| Module | What it does |
| --- | --- |
| `domain/tariff.py` | Pure logic with no IO: `Band`, `Tariff`, `consumption` (meter delta with rollover), `charge_for` (progressive band pricing plus standing charge), `period_days`. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Account`, `Meter`, `Reading`, `Bill`), the engine and session factory, `ensure_aware_utc`, and `session_scope()`. |
| `repo.py` | `AccountRepo`, `MeterRepo`, `ReadingRepo`, `BillRepo`, the only place that builds queries. |
| `service.py` | `ReadingService` (submit, correct) and `BillingService` (generate), the business rules layered on the repositories. |
| `api.py` | FastAPI app. `X-Metering-Key` auth with two roles, reader and customer. |
| `estimates.py` | `estimate_missing`, a batch job that fills in a reading for a meter that has gone quiet. |
| `tests/` | pytest suite for all of the above. In-memory SQLite with `StaticPool`. |

## Conventions

These conventions are deliberately different from `app/`, `sandbox/rooms/`,
`sandbox/lockers/`, `sandbox/library/`, and `sandbox/expenses/`. Exercises
built on this codebase will break them.

1. **Ids are auto-incrementing integer primary keys.** No UUIDs.
2. **Readings are an append-only ledger.** A reading row is never updated
   or deleted. A correction is a new row whose `supersedes_id` points at
   the row it replaces. Every query for "the reading" as of some point
   must therefore exclude any row referenced by another row's
   `supersedes_id` and take the latest surviving row; `repo.ReadingRepo`
   is the only place that does this, and it is never done by filtering on
   an `is_current` flag or similar, because there isn't one.
3. **Quantities and money are `Decimal`, never floats or int cents.**
   Consumption (`value_kwh`, `Bill.kwh`) is `Decimal` at 3 decimal places.
   Money (`Bill.amount`, tariff rates and standing charges) is `Decimal`
   quantized to cents, rounding half up.
4. **Datetimes are timezone-aware UTC; billing periods are half-open
   `[start, end)` on plain dates.** `db.ensure_aware_utc` rejects a naive
   datetime at the service boundary. `Bill.period_start` and
   `Bill.period_end` are `date`, not `datetime`; `end` is never included in
   the period itself.
5. **The API dependency owns the transaction.** `db.session_scope()` is a
   context manager that yields a session, commits on success, and rolls
   back on error. `api.get_db` is the only place that wraps it as a
   FastAPI dependency. `ReadingService`, `BillingService`, and every
   repository flush but never commit.
6. **Bills are idempotent per (account, period).** `BillingService.generate`
   checks for an existing bill for the same account and the same
   `(period_start, period_end)` before computing anything; calling it
   twice for the same period returns the first bill, not a second one. The
   database also carries a unique constraint on the triple as a backstop.
7. **Two roles, one key list.** `METERING_KEYS` is a comma-separated list
   of `role:email:key` triples, `role` being `reader` (submits and
   corrects readings, generates bills, can read any account's bills) or
   `customer` (reads only their own account's bills).
8. **Every public function has a test.** Public means no leading
   underscore and importable from the module.
9. **No f-string or `%`-formatted SQL.** Every query goes through
   SQLAlchemy Core or ORM constructs with bound parameters.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `METERING_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `METERING_KEYS` | empty | comma-separated `role:email:key` triples |

## Running

```
uv run pytest sandbox/metering/tests
uv run mypy sandbox/metering/domain
uv run uvicorn sandbox.metering.api:app --reload
```
