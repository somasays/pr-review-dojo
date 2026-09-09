# adserver

A publisher-side ad serving service: advertisers run campaigns with a
daily budget and a CPM, and every ad request asks whether one more
impression can be served without going over that budget. This is an
eleventh, self-contained codebase used as a base for a different set of
practice exercises than `app/`, `sandbox/rooms/`, `sandbox/lockers/`,
`sandbox/library/`, `sandbox/expenses/`, `sandbox/metering/`,
`sandbox/timesheets/`, `sandbox/parking/`, `sandbox/helpdesk/`, and
`sandbox/newsroom/`. It does not import anything from any of them.

Serving decisions cannot wait on a database write for every impression,
so counts are kept in an in-process tracker guarded by a single lock and
only reconciled into the database on an interval by a background
flusher. A serve decision therefore checks the budget against the sum of
what has already been flushed for today and what the tracker has counted
since the last flush.

## Vocabulary

- **Advertiser**: the account a campaign belongs to.
- **Campaign**: one advertiser's line item, with a `daily_budget`, a
  `cpm`, and a lifecycle of `active`, `paused`, `ended`, changed only
  through `domain.pacing.transition`.
- **Daily budget**: the most a campaign may spend in one UTC calendar
  day.
- **CPM**: the price of 1000 impressions.
- **Impression**: one ad served for a campaign.
- **Spend**: money owed for impressions already served, per campaign per
  UTC day.
- **Pacing**: keeping spend on a linear trajectory across the day rather
  than exhausting the budget in the first minute.
- **Flush**: writing the tracker's in-memory impression counts into the
  `spend` table.

## Layout

| Module | What it does |
| --- | --- |
| `domain/pacing.py` | Pure logic with no IO: `cost_of`, `can_serve`, `remaining_budget`, `pace_target`, `CampaignStatus`, `transition`. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Advertiser`, `Campaign`, `Spend`), the engine and session factory, `create_all`, `unit_of_work()`. |
| `repo.py` | `AdvertiserRepo`, `CampaignRepo`, `SpendRepo`, the only place that builds queries. |
| `tracker.py` | `SpendTracker`, the lock-guarded impression counter, and `Flusher`, the background thread that reconciles counts into `spend` rows. |
| `service.py` | `ServingService` (decide) and `CampaignService` (create, pause, resume, end). |
| `api.py` | FastAPI app. `X-Adserver-Key` auth, ops and advertiser roles. |
| `tests/` | pytest suite for all of the above. In-memory SQLite. |

## Conventions

1. **Ids are auto-incrementing integer primary keys.** No UUIDs.
2. **Money is `Decimal` quantized to cents.** `cpm` is the price per 1000
   impressions, also a `Decimal` quantized to cents.
3. **Datetimes are timezone-aware UTC.** A spend day is a UTC calendar
   date (`date`, not `datetime`).
4. **A campaign's status is `active`, `paused`, or `ended`, changed only
   through `domain.pacing.transition`.** No other code assigns
   `Campaign.status` directly.
5. **Impressions are counted in an in-process `SpendTracker` guarded by
   one lock, taken by every reader and writer.** A background `Flusher`
   thread writes the counts to `spend` rows every interval, opening its
   own session per iteration. The tracker keeps its counts until a flush
   succeeds.
6. **Repositories flush only.** The service owns the transaction through
   `unit_of_work()` in `db.py`.
7. **A `spend` row is per campaign per UTC day, and is upserted only by
   the flusher.** Request handlers never write to `spend` directly.
8. **Two roles.** `ADSERVER_KEYS` is a comma-separated list of
   `role:email:key` triples, role being `ops` or `advertiser`. An
   advertiser sees only their own campaigns.
9. **Every public function has a test.** Public means no leading
   underscore and importable from the module.
10. **No f-string or `%`-formatted SQL.** Every query goes through
    SQLAlchemy Core or ORM constructs with bound parameters.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `ADSERVER_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `ADSERVER_KEYS` | empty | comma-separated `role:email:key` triples |

## Running

```
uv run pytest sandbox/adserver/tests
uv run mypy sandbox/adserver/domain
uv run uvicorn sandbox.adserver.api:app --reload
```
