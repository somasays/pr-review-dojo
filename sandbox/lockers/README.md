# lockers

A parcel locker service: a courier deposits a parcel into the smallest
compartment it fits, the recipient picks it up with a six-digit code, and a
sweeper job warns recipients before a parcel expires and clears out the ones
that were never collected. This is a third, self-contained codebase used as
a base for a different set of practice exercises than `app/` and
`sandbox/rooms/`. It does not import anything from either of them.

## Layout

| Module | What it does |
| --- | --- |
| `domain/fit.py` | Pure logic with no IO: `Size`, `Dimensions`, `fits`, `smallest_size_for`, `late_fee_cents`, and `expires_at`. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Locker`, `Compartment`, `Parcel`), the engine and session factory, `ensure_naive_utc`, and `unit_of_work()`. |
| `repo.py` | `CompartmentRepo` and `ParcelRepo`, the only place that builds queries. |
| `service.py` | `DepositService` (picks a compartment, generates a code, stores the parcel) and `PickupService` (validates the code, prices the late fee, frees the compartment). |
| `api.py` | FastAPI app. `X-Courier-Key` auth for deposits and the compartment summary; pickup is public. |
| `sweeper.py` | `sweep`, a job that warns recipients of parcels expiring soon and hard-deletes the ones that expired. |
| `tests/` | pytest suite for all of the above. In-memory SQLite with `StaticPool`. |

## Conventions

These conventions are deliberately different from both `app/` and
`sandbox/rooms/`. Exercises built on this codebase will break them.

1. **Ids are auto-incrementing integers.** No UUIDs, no natural keys.
2. **Money is integer cents, never `Decimal`.** There is no `Money` type
   here, and no float ever touches a price.
3. **All datetimes are naive and UTC by convention, never aware.** A value
   is the moment you get from `datetime.utcnow()`; there is no `tzinfo` and
   no timezone-aware column anywhere. `db.ensure_naive_utc` rejects an aware
   datetime at the service boundary. This is the opposite of
   `sandbox/rooms`, where `Slot` requires timezone-aware UTC.
4. **The service owns the transaction.** `db.unit_of_work()` is a context
   manager that yields a session, commits on success, and rolls back on
   error. `DepositService` and `PickupService` each open exactly one per
   call. Repositories never commit, matching `app/db/repositories.py` and
   unlike `sandbox/rooms/repo.py`, where every repository method commits.
5. **Hard deletes are allowed for expired parcels.** Once a parcel's hold
   expires and is swept, the row is gone; there is no soft-delete column.
   This is the opposite of `sandbox/rooms`, where cancellation is soft.
6. **Every public function has a test.** Public means no leading underscore
   and importable from the module.
7. **No f-string or `%`-formatted SQL.** Every query goes through
   SQLAlchemy Core or ORM constructs with bound parameters.
8. **Pickup codes are 6 digits, generated with `secrets`, and unique per
   locker among active parcels.** "Active" means not yet picked up; an
   expired-but-not-yet-swept parcel still holds its code until the sweeper
   deletes it.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOCKERS_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `LOCKERS_COURIER_KEYS` | empty | comma-separated courier API keys |

## Running

```
uv run pytest sandbox/lockers/tests
uv run mypy sandbox/lockers/domain
uv run uvicorn sandbox.lockers.api:app --reload
```
