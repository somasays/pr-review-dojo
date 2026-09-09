# parking

A parking garage service: a car enters and gets a ticket, the ticket is
priced off a rate card when it is paid, and the car has a short window to
leave before the fee is recomputed for the extra time. This is an eighth,
self-contained codebase used as a base for a different set of practice
exercises than `app/`, `sandbox/rooms/`, `sandbox/lockers/`,
`sandbox/library/`, `sandbox/expenses/`, `sandbox/metering/`, and
`sandbox/timesheets/`. It does not import anything from any of them.

The garage has a fixed capacity and never issues two open tickets for the
same plate at once. A ticket's price is computed once, at payment time,
from the time parked so far; if the car does not leave within 15 minutes of
paying, the fee is recomputed to cover the extra time before the ticket can
close.

## Vocabulary

- **Garage**: a physical facility with a fixed `capacity`, the number of
  cars it can hold at once.
- **Ticket**: one parking visit for a plate in a garage. Its lifecycle is
  `open`, then `paid`, then `exited`, and only `domain.pricing.transition`
  may move it between those states.
- **Plate**: the vehicle identifier a garage's open tickets are keyed by;
  a garage never has two open tickets for the same plate.
- **Rate card**: the pricing rule a garage bills against: a grace period,
  a first-hour rate, a per-started-hour rate after that, a daily cap, and
  a flat fee for a lost ticket (`domain.pricing.RateCard`).
- **Session**: the span of time a ticket covers, from `entered_at` to
  `exited_at`.
- **Pass**: a prepaid block of time for a plate that covers its parking
  without a per-visit fee. Not part of this base.

## Layout

| Module | What it does |
| --- | --- |
| `domain/pricing.py` | Pure logic with no IO: `RateCard`, `billable_minutes`, `fee_for` (grace, first hour, started extra hours, daily cap), `TicketStatus`, `transition`. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Garage`, `Ticket`), the engine and session factory, `ensure_aware_utc`, `coerce_utc`, and `session_scope()`. |
| `repo.py` | `GarageRepo`, `TicketRepo`, the only place that builds queries. |
| `service.py` | `ParkingService` (enter, pay, exit), the business rules layered on the repositories. |
| `api.py` | FastAPI app. `X-Attendant-Key` auth, one role. |
| `tests/` | pytest suite for all of the above. In-memory SQLite with `StaticPool`. |

## Conventions

1. **Ids are auto-incrementing integer primary keys.** No UUIDs.
2. **Money is `Decimal`, quantized to cents.** A `RateCard`'s amounts are
   integer cents; `fee_for` returns a `Decimal` quantized to `0.01`,
   rounding half up.
3. **Datetimes are timezone-aware UTC.** `db.ensure_aware_utc` rejects a
   naive datetime at the service boundary; `db.coerce_utc` reattaches the
   UTC tzinfo SQLite drops on a round trip.
4. **A ticket's lifecycle is `open`, then `paid`, then `exited`, changed
   only through `domain.pricing.transition`.** No other code assigns
   `Ticket.status` directly.
5. **Repositories flush only.** The API's `get_db` dependency owns the
   transaction through `session_scope()`; `ParkingService` and every
   repository never commit.
6. **One open ticket per plate per garage.** `ParkingService.enter` checks
   this before creating a new ticket.
7. **Every public function has a test.** Public means no leading
   underscore and importable from the module.
8. **No f-string or `%`-formatted SQL.** Every query goes through
   SQLAlchemy Core or ORM constructs with bound parameters.
9. **One role, one key list.** `PARKING_KEYS` is a comma-separated list of
   attendant keys; every endpoint requires a valid `X-Attendant-Key`.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `PARKING_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `PARKING_KEYS` | empty | comma-separated attendant keys |

## Running

```
uv run pytest sandbox/parking/tests
uv run mypy sandbox/parking/domain
uv run uvicorn sandbox.parking.api:app --reload
```
