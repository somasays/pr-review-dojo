# timesheets

An hourly-worker timesheet and overtime service: a worker clocks shifts, a
timesheet rolls a Monday-to-Sunday pay period's shifts into regular,
overtime, and night-differential pay, and a manager approves or rejects it.
This is a seventh, self-contained codebase used as a base for a different
set of practice exercises than `app/`, `sandbox/rooms/`, `sandbox/lockers/`,
`sandbox/library/`, `sandbox/expenses/`, and `sandbox/metering/`. It does
not import anything from any of them.

## Vocabulary

- **Worker**: a person who clocks shifts and is paid by the hour. Carries
  an IANA `timezone` and a `Decimal` `hourly_rate`. `Worker.active` is the
  only restriction on who may record a shift.
- **Shift**: one span of clocked time, `start_utc` to `end_utc`, stored as
  aware UTC datetimes with a redundant integer-minute `minutes` column. A
  shift belongs to the local calendar day it starts on, not the day it
  ends on. A shift that crosses the worker's local midnight is recorded as
  two rows, split at that boundary, so each part belongs to its own local
  day for daily overtime and its own night-differential minutes. The two
  rows share a `group_id` (the first row's own id) so the original clock-in
  can still be shown as one entry. If the split lands in a different pay
  period, the second row is added to that period's open timesheet.
- **Pay period**: a Monday-to-Sunday, half-open week. `period_start` is
  always a Monday; the period runs through the following Sunday night.
- **Timesheet**: one worker's shifts for one pay period. Its truth is
  `Timesheet.status`; `total_pay` is the aggregate the domain layer
  computed at submission, not something entered by hand.
- **Overtime**: minutes worked past a daily threshold on a local calendar
  day, or past a weekly threshold across the whole period, paid at
  `overtime_multiplier` times the hourly rate. Daily overtime is computed
  first; whatever regular minutes remain are then checked against the
  weekly threshold.
- **Differential**: an additional per-hour amount paid for minutes worked
  inside the nightly window, on top of whatever those minutes already
  earned as regular or overtime pay.
- **Approval**: a manager's decision on a submitted timesheet, approve or
  reject, made through `domain.pay.transition`. A manager may never decide
  a timesheet belonging to their own worker record.

## Layout

| Module | What it does |
| --- | --- |
| `domain/pay.py` | Pure logic with no IO: `Rules`, `local_day`, `shift_minutes`, `night_minutes`, `split_overtime`, `weekly_overtime`, `pay_cents`, and `TimesheetStatus` with the single `transition` function. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Worker`, `Timesheet`, `Shift`), the engine and session factory, `ensure_aware_utc`, and `unit_of_work()`. |
| `repo.py` | `WorkerRepo`, `TimesheetRepo`, `ShiftRepo`, the only place that builds queries. |
| `service.py` | `ShiftService` (record) and `TimesheetService` (submit, decide, correct), the business rules layered on the repositories. `period_totals` aggregates a timesheet's shifts into regular, overtime, and night minutes for the period. |
| `api.py` | FastAPI app. `X-Timesheets-Key` auth with two roles, worker and manager. |
| `payroll.py` | `export_period`, an idempotent CSV export of one pay period's approved timesheets. |
| `tests/` | pytest suite for all of the above. In-memory SQLite with `StaticPool`. |

## Conventions

These conventions are deliberately different from `app/`, `sandbox/rooms/`,
`sandbox/lockers/`, `sandbox/library/`, `sandbox/expenses/`, and
`sandbox/metering/`. Exercises built on this codebase will break them.

1. **Ids are auto-incrementing integer primary keys.** No UUIDs.
2. **Durations are integer minutes, stored as a plain integer column.**
   Never a float and never a `timedelta` in the database; `Shift.minutes`
   is computed once, at record time, by `domain.pay.shift_minutes`.
3. **Money is `Decimal`, quantized to cents; hourly rates are `Decimal`
   with 4 decimal places.** `Worker.hourly_rate` is `Numeric(9, 4)`;
   `Timesheet.total_pay` is `Numeric(12, 2)`.
4. **Shift clock times are aware datetimes stored in UTC, but pay rules
   are evaluated in the worker's local timezone.** `Worker.timezone` is an
   IANA zone name resolved with `zoneinfo`. A shift belongs to the local
   calendar day it starts on, even if it crosses midnight; `domain.pay`
   never uses a naive datetime or the server's local timezone.
5. **Pay periods are Monday to Sunday, half-open.** `period_start` is
   always the Monday that starts the week containing a shift's local day.
6. **A timesheet is mutable while `open`, frozen once `submitted`, and
   `approved` or `rejected` by a manager through one `transition`
   function.** Corrections after approval are a new timesheet version,
   never an edit of the approved one: `TimesheetService.correct` clones
   the approved timesheet's shifts into a fresh `open` version with
   `version + 1`.
7. **The service owns the transaction.** `db.unit_of_work()` is a context
   manager that yields a session, commits on success, and rolls back on
   error. `ShiftService` and `TimesheetService` each open exactly one per
   call. Repositories flush but never commit.
8. **Two roles, one key list.** `TIMESHEETS_KEYS` is a comma-separated
   list of `role:email:key` triples, `role` being `worker` or `manager`. A
   manager may never approve or reject a timesheet belonging to their own
   worker record.
9. **Every public function has a test.** Public means no leading
   underscore and importable from the module.
10. **No f-string or `%`-formatted SQL.** Every query goes through
    SQLAlchemy Core or ORM constructs with bound parameters.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `TIMESHEETS_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `TIMESHEETS_KEYS` | empty | comma-separated `role:email:key` triples |

## Running

```
uv run pytest sandbox/timesheets/tests
uv run mypy sandbox/timesheets/domain
uv run uvicorn sandbox.timesheets.api:app --reload
```
