# helpdesk

A support ticket queue: a ticket is opened against a subject and a
priority, an agent claims it off the shared queue, and the agent who
claimed it (or a lead) resolves it. This is a ninth, self-contained
codebase used as a base for a different set of practice exercises than
`app/`, `sandbox/rooms/`, `sandbox/lockers/`, `sandbox/library/`,
`sandbox/expenses/`, `sandbox/metering/`, `sandbox/timesheets/`, and
`sandbox/parking/`. It does not import anything from any of them.

A ticket's due time is set from its priority when it is opened, and a
background job walks the queue for tickets past due, escalating each one
once per level it crosses. Every ticket has at most one agent, an agent
claims by taking it off the unassigned queue, and only a lead may resolve
a ticket someone else claimed.

## Vocabulary

- **Ticket**: one support request, with a `subject`, a `priority`, and a
  lifecycle of `open`, then `claimed`, then `resolved`, changed only
  through `domain.sla.transition`.
- **Agent**: the person who claims and resolves tickets.
- **Queue**: the unassigned open tickets, in the order they were opened.
- **Claim**: an agent taking an open ticket off the queue; a ticket has
  at most one agent.
- **Priority**: `low`, `normal`, or `high`, which sets a ticket's due
  time (`domain.sla.due_at`).
- **Due**: the timestamp by which a ticket should be resolved.
- **Escalation**: the level a breached ticket has reached, 0 before due,
  1 within 4 hours past due, 2 beyond that (`domain.sla.escalation_level`).

## Layout

| Module | What it does |
| --- | --- |
| `domain/sla.py` | Pure logic with no IO: `Priority`, `due_at`, `is_breached`, `escalation_level`, `TicketStatus`, `transition`. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Agent`, `Ticket`), the engine and session factory, `ensure_aware_utc`, `coerce_utc`, and `unit_of_work()`. |
| `repo.py` | `AgentRepo`, `TicketRepo`, the only place that builds queries. |
| `service.py` | `TicketService` (create, claim, resolve), the business rules layered on the repositories. |
| `metrics.py` | `QueueMetrics`, an in-process counter component guarded by one lock. |
| `escalation.py` | `escalate_breached`, the job that walks breached tickets and notifies on each level change. |
| `api.py` | FastAPI app. `X-Helpdesk-Key` auth, agent and lead roles. |
| `tests/` | pytest suite for all of the above. In-memory SQLite with `StaticPool`. |

## Conventions

1. **Ids are auto-incrementing integer primary keys.** No UUIDs.
2. **Datetimes are timezone-aware UTC.** `db.ensure_aware_utc` rejects a
   naive datetime at the service boundary; `db.coerce_utc` reattaches the
   UTC tzinfo SQLite drops on a round trip.
3. **A ticket's status is `open`, `claimed`, or `resolved`, changed only
   through `domain.sla.transition`.** No other code assigns
   `Ticket.status` directly.
4. **Repositories flush only.** The service owns the transaction through
   `unit_of_work()` in `db.py`. A `Session` is never shared across
   threads: any background thread opens its own session per iteration.
5. **A ticket has at most one agent.**
6. **In-process shared state (counters, caches) is guarded by one lock
   per component, taken by every reader and writer.**
7. **Two roles.** `HELPDESK_KEYS` is a comma-separated list of
   `role:email:key` triples, role being `agent` or `lead`.
8. **Every public function has a test.** Public means no leading
   underscore and importable from the module.
9. **No f-string or `%`-formatted SQL.** Every query goes through
   SQLAlchemy Core or ORM constructs with bound parameters.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `HELPDESK_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `HELPDESK_KEYS` | empty | comma-separated `role:email:key` triples |

## Running

```
uv run pytest sandbox/helpdesk/tests
uv run mypy sandbox/helpdesk/domain
uv run uvicorn sandbox.helpdesk.api:app --reload
```
