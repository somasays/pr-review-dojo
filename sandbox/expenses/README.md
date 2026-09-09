# expenses

An employee expense reimbursement service: an employee submits a claim
with one or more spending lines, an approver approves or rejects it, and a
payout job collects every approved claim into a batch and exports it for
payment. This is a fifth, self-contained codebase used as a base for a
different set of practice exercises than `app/`, `sandbox/rooms/`,
`sandbox/lockers/`, and `sandbox/library/`. It does not import anything
from any of them.

## Vocabulary

- **Employee**: a person who files claims. `Employee.active` is the only
  restriction on who may submit; `manager_email` is informational and does
  not participate in approval.
- **Claim**: one reimbursement request from one employee, in exactly one
  currency. Its truth is `Claim.status`; `submitted_at` and `decided_at`
  are records of when, not the state itself.
- **Line**: one spending item on a claim, with a `Category`, an `amount`,
  and the date the expense was incurred. A claim's total is the sum of its
  lines; there is no separate total column.
- **Policy**: the per-line and per-month caps for a category
  (`domain.policy.PolicyLimit`). The per-line cap is checked once, at
  submission. The per-month cap is checked at submission against the
  submitted lines, and again at approval against whichever lines the
  approver actually approves, since a partial approval can pull a claim
  back under the cap it would have exceeded in full.
- **Approval**: an approver's decision on a submitted claim, in whole or
  line by line. A rejected line carries a reason; `Claim.payable_total` is
  the sum of the approved lines, not the submitted total, and a claim is
  rejected only when every line on it is rejected. Approval is not a role
  assigned to a claim; it is the act of deciding one. An approver may never
  decide a claim they themselves filed. Repeating the same decision from
  the same approver on an already-decided claim returns it unchanged.
- **Batch**: a group of approved, unpaid claims collected for one payout
  run. Creating a batch is what moves a claim from `approved` to `paid`;
  there is no separate "mark paid" step. Each claim pays its
  `payable_total`, not the total it was submitted with.

## Layout

| Module | What it does |
| --- | --- |
| `domain/policy.py` | Pure logic with no IO: `Category`, `PolicyLimit`, `line_violations`, `month_total_ok`, `convert` (currency conversion with half-up rounding), and `ClaimStatus` with the single `transition` function. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Employee`, `Claim`, `ClaimLine`, `PayoutBatch`), the engine and session factory, `ensure_aware_utc`, and `unit_of_work()`. |
| `repo.py` | `EmployeeRepo`, `ClaimRepo`, `BatchRepo`, the only place that builds queries. |
| `service.py` | `ClaimService` (submit, decide) and `PayoutService` (create_batch), the business rules layered on the repositories. |
| `api.py` | FastAPI app. `X-Expenses-Key` auth with two roles, employee and approver. |
| `export.py` | `write_batch_csv`, an idempotent CSV export of one paid batch. |
| `tests/` | pytest suite for all of the above. In-memory SQLite with `StaticPool`. |

## Conventions

These conventions are deliberately different from `app/`,
`sandbox/rooms/`, `sandbox/lockers/`, and `sandbox/library/`. Exercises
built on this codebase will break them.

1. **Ids are UUID4 strings.** No auto-incrementing integer primary keys.
2. **Money is `Decimal`, quantized to cents, with an explicit currency
   code on every amount.** A `Claim` has exactly one currency; every line
   on it is denominated in that currency. There is no implicit conversion
   between claims of different currencies.
3. **Datetimes are timezone-aware UTC.** `db.ensure_aware_utc` rejects a
   naive datetime at the service boundary.
4. **The service owns the transaction.** `db.unit_of_work()` is a context
   manager that yields a session, commits on success, and rolls back on
   error. `ClaimService` and `PayoutService` each open exactly one per
   call. Repositories flush but never commit.
5. **Claim state is an enum, and every change goes through one function.**
   `ClaimStatus` is `draft`, `submitted`, `approved`, `rejected`, or
   `paid`. `domain.policy.transition(current, target)` is the only place
   that decides whether a status change is legal; nothing sets
   `Claim.status` directly.
6. **Submissions carry a client-supplied idempotency key, unique per
   employee.** Resubmitting the same `(employee, idempotency_key)` pair
   returns the original claim instead of creating a second one.
7. **Every public function has a test.** Public means no leading
   underscore and importable from the module.
8. **No f-string or `%`-formatted SQL.** Every query goes through
   SQLAlchemy Core or ORM constructs with bound parameters.
9. **Two roles, one key list.** `EXPENSES_KEYS` is a comma-separated list
   of `role:email:key` triples, `role` being `employee` or `approver`. An
   approver may never approve or reject a claim filed by that same
   approver's own email.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `EXPENSES_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `EXPENSES_KEYS` | empty | comma-separated `role:email:key` triples |

## Running

```
uv run pytest sandbox/expenses/tests
uv run mypy sandbox/expenses/domain
uv run uvicorn sandbox.expenses.api:app --reload
```
