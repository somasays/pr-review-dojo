# rooms

A meeting-room booking service: reserve a room for a half-hour-aligned time
slot, cancel your own bookings, check what is free on a given day, and remind
holders shortly before their booking starts. This is a second, self-contained
codebase used as a base for a different set of practice exercises than
`app/`. It does not import anything from `app/`.

## Layout

| Module | What it does |
| --- | --- |
| `domain/slots.py` | Pure logic with no IO: the `Slot` dataclass (validation, overlap, half-hour splitting) and `price_cents`. Type-checked with mypy strict. |
| `db.py` | SQLAlchemy 2.x models (`Room`, `Booking`), the engine and session factory, `create_all`. |
| `repo.py` | `RoomRepo` and `BookingRepo`, the only place that builds queries. |
| `service.py` | `BookingService`: books a slot (checks the room, rejects overlaps, prices it) and cancels a booking (holder only). |
| `api.py` | FastAPI app. `X-API-Key` auth, endpoints for creating, reading, and cancelling bookings, and room availability. |
| `reminders.py` | `send_reminders`, a job that notifies holders of bookings starting within the hour, once each. |
| `tests/` | pytest suite for all of the above. In-memory SQLite with `StaticPool`. |

## Conventions

These conventions are deliberately different from `app/`. Exercises built on
this codebase will break them.

1. **Money is integer cents, never `Decimal`.** `price_cents` and the
   `price_cents` column are plain `int`. There is no `Money` type here.
2. **Repositories commit.** Every `RoomRepo` and `BookingRepo` method is its
   own unit of work: call it, and the change is durable. This is the
   opposite of `app/db/repositories.py`, where the caller owns the
   transaction. Do not add a transaction wrapper around repo calls here.
3. **Ids are UUID4 strings.** No auto-incrementing integer primary keys.
4. **All times are timezone-aware UTC, slots are half-hour aligned.** `Slot`
   rejects naive datetimes and times that do not land on `:00` or `:30`.
5. **Deletes are soft.** Cancelling a booking sets `cancelled_at`; rows are
   never deleted from the table.
6. **Every public function has a test.** Public means no leading underscore
   and importable from the module.
7. **No f-string or `%`-formatted SQL.** Every query goes through SQLAlchemy
   Core or ORM constructs with bound parameters.

## Settings

| Variable | Default | Purpose |
| --- | --- | --- |
| `ROOMS_DATABASE_URL` | `sqlite://` (in memory) | SQLAlchemy URL |
| `ROOMS_API_KEYS` | empty | comma-separated `email:key` pairs |

## Running

```
uv run pytest sandbox/rooms/tests
uv run mypy sandbox/rooms/domain
uv run uvicorn sandbox.rooms.api:app --reload
```
