# library

A library lending service: a patron checks out an item, returns it (owing a
fine if it comes back late), or places a hold to reserve the next copy that
becomes free. This is a fourth, self-contained codebase used as a base for a
different set of practice exercises than `app/`, `sandbox/rooms/`, and
`sandbox/lockers/`. It does not import anything from any of them.

## Vocabulary

- **Patron**: a person who borrows. `Patron.blocked` is the only restriction on a patron.
- **Item**: a title the library owns. `Item.copies` is how many physical copies exist. There is no copy entity; a copy is only ever "copies minus active loans" (`ItemRepo.available_copies`).
- **Loan**: one copy out with one patron. Its truth is `Loan.status` (`active`, `returned`, `lost`); `returned_on` is a record of when, not a state. `Loan.renewals` counts how many times the due date was extended.
- **Hold**: a place in the queue for the next free copy of an item. A hold is a reservation by a patron who does not have the item; it is not a loan and not a block. "Another patron holds the item" means an active `Hold` row by someone else, not that someone currently has a copy out.
- **Dates versus datetimes**: loans live in calendar days (`checked_out_on`, `due_on`, `returned_on` are `date`); holds live in moments (`placed_at`, `fulfilled_at` are aware UTC `datetime`). Fine and due-date math is in days; hold ordering is in seconds. Never compare one with the other.

## Layout

| Module | What it does |
| --- | --- |
| `domain/lending.py` | Pure logic with no IO: `LoanStatus`, the single `transition` function every status change goes through, `due_date`, `fine_for`, and `can_renew`. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Patron`, `Item`, `Loan`, `Hold`), the engine and session factory, `ensure_aware_utc`, and `session_scope()`. |
| `repo.py` | `PatronRepo`, `ItemRepo`, `LoanRepo`, `HoldRepo`, the only place that builds queries. |
| `service.py` | `LendingService`: checks out an item (blocked patrons refused, hold queue respected, copies checked), returns a loan (computes the fine), and places a hold. |
| `api.py` | FastAPI app. `X-Library-Key` auth with two roles, librarian and patron. |
| `overdue.py` | `notify_overdue`, a job that warns patrons of overdue loans, once per loan per day. |
| `tests/` | pytest suite for all of the above. In-memory SQLite with `StaticPool`. |

## Conventions

These conventions are deliberately different from `app/`, `sandbox/rooms/`,
and `sandbox/lockers/`. Exercises built on this codebase will break them.

1. **Ids are auto-incrementing integers.** No UUIDs, no natural keys.
2. **Money is `Decimal`, quantized to cents, never an int.** A fine is a
   `Decimal` produced by `fine_for` and quantized with `ROUND_HALF_UP`;
   nothing here ever counts money in integer cents.
3. **Datetimes are timezone-aware UTC; due dates are plain `date`.**
   `db.ensure_aware_utc` rejects a naive datetime at the service boundary.
   A due date, a checkout date, and a return date are calendar dates with
   no time component and no timezone, because a library due date is a day,
   not a moment.
4. **Repositories never commit.** `db.session_scope()` is the only place a
   transaction opens or closes, and only the API's `get_db` dependency and
   the `overdue` job call it. `LendingService` runs inside whatever session
   its caller opened and never commits or rolls back itself. This is
   different from both `sandbox/rooms` (every repository method commits)
   and `sandbox/lockers` (the service opens a unit of work per call);
   here, the transaction boundary sits one layer higher, at the request or
   the job.
5. **Nothing is ever hard-deleted.** A returned loan stays in the table
   with `status="returned"`; a fulfilled hold stays with `fulfilled_at`
   set. There is no delete anywhere in this codebase.
6. **Loan state is an enum, and every transition goes through one
   function.** `LoanStatus` is `active`, `returned`, or `lost`.
   `domain.lending.transition(current, target)` is the only place that
   decides whether a status change is legal; nothing sets `Loan.status`
   directly.
7. **Every public function has a test.** Public means no leading
   underscore and importable from the module.
8. **No f-string or `%`-formatted SQL.** Every query goes through
   SQLAlchemy Core or ORM constructs with bound parameters.
9. **Two roles, one key list.** `LIBRARY_KEYS` is a comma-separated list of
   `role:email:key` triples, `role` being `librarian` or `patron`. A
   librarian key can act for any patron; a patron key can only act for
   itself.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `LIBRARY_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `LIBRARY_KEYS` | empty | comma-separated `role:email:key` triples |

## Running

```
uv run pytest sandbox/library/tests
uv run mypy sandbox/library/domain
uv run uvicorn sandbox.library.api:app --reload
```
