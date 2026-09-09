# newsroom

A news app home screen curation service: editors publish articles and
place them into slots on a section's home screen for a time window, and
readers get back a ranked list of what should show right now. This is a
tenth, self-contained codebase used as a base for a different set of
practice exercises than `app/`, `sandbox/rooms/`, `sandbox/lockers/`,
`sandbox/library/`, `sandbox/expenses/`, `sandbox/metering/`,
`sandbox/timesheets/`, `sandbox/parking/`, and `sandbox/helpdesk/`. It
does not import anything from any of them.

A slot holds at most one placement at any instant, so placing an article
into an occupied slot for an overlapping window is refused. What readers
see never comes straight from the database: a background thread rebuilds
an in-process snapshot of the home screen for every section on an
interval, and every request is served from that snapshot rather than
hitting the database on every read.

## Vocabulary

- **Article**: one piece of content, with a `headline` and a lifecycle of
  `draft`, then `published`, then `retracted`, changed only through
  `domain.curation.transition`.
- **Section**: a named area of the home screen with a fixed number of
  slots (`slot_count`).
- **Slot**: one position within a section, numbered from 1.
- **Placement**: an article assigned to a slot in a section for a half-open
  time window.
- **Pin**: a placement marked to rank ahead of everything else in its
  section while it is live.
- **Embargo**: a placement's window has not started yet, so it is not
  live even though the article is published.
- **Home screen**: the ranked, live placements for a section, as served
  to readers.
- **Snapshot**: the in-process, per-section home screen built by the
  background refresher and served from `HomeScreenCache`.

## Layout

| Module | What it does |
| --- | --- |
| `domain/curation.py` | Pure logic with no IO: `ArticleStatus`, `transition`, `is_live`, `windows_overlap`, `rank_key`, `PlacementView`, `build_home_screen`. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Article`, `Section`, `Placement`), the engine and session factory, `ensure_aware_utc`, `coerce_utc`, and `unit_of_work()`. |
| `repo.py` | `ArticleRepo`, `SectionRepo`, `PlacementRepo`, the only place that builds queries. |
| `cache.py` | `HomeScreenCache`, the lock-guarded snapshot, and `Refresher`, the background thread that rebuilds it. |
| `service.py` | `CurationService` (publish, place, retract), the business rules layered on the repositories. |
| `api.py` | FastAPI app. `X-Newsroom-Key` auth, editor and reader roles. |
| `tests/` | pytest suite for all of the above. In-memory SQLite. |

## Conventions

1. **Ids are auto-incrementing integer primary keys.** No UUIDs.
2. **Datetimes are timezone-aware UTC.** Time windows are half-open
   `[start, end)`. `db.ensure_aware_utc` rejects a naive datetime at the
   service boundary; `db.coerce_utc` reattaches the UTC tzinfo SQLite
   drops on a round trip.
3. **An article's status is `draft`, `published`, or `retracted`,
   changed only through `domain.curation.transition`.** No other code
   assigns `Article.status` directly.
4. **Repositories flush only.** The service owns the transaction through
   `unit_of_work()` in `db.py`. A `Session` is never shared across
   threads: any background thread opens its own session per iteration.
5. **A slot holds at most one placement at any instant.**
6. **The home screen served to readers comes from an in-process
   `HomeScreenCache` snapshot rebuilt by a background refresher.** The
   snapshot is replaced atomically as one object under the component's
   lock, and every reader takes the same lock.
7. **Readers never see draft or retracted articles, nor a placement
   outside its window.**
8. **Two roles.** `NEWSROOM_KEYS` is a comma-separated list of
   `role:email:key` triples, role being `editor` or `reader`.
9. **Every public function has a test.** Public means no leading
   underscore and importable from the module.
10. **No f-string or `%`-formatted SQL.** Every query goes through
    SQLAlchemy Core or ORM constructs with bound parameters.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEWSROOM_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `NEWSROOM_KEYS` | empty | comma-separated `role:email:key` triples |

## Running

```
uv run pytest sandbox/newsroom/tests
uv run mypy sandbox/newsroom/domain
uv run uvicorn sandbox.newsroom.api:app --reload
```
