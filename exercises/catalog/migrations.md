# Migrations defect catalog

Defects for exercises whose feature PR touches `app/db/alembic/versions/` and
`app/db/models.py`. The base repo has two revisions, `0001` (initial schema)
and `0002` (index `ix_orders_customer_created`), so every planted migration
below is `0003` unless stated otherwise. Production is Postgres with large
`orders` and `order_items` tables; the test target is SQLite through
`tests/test_migrations.py`, which runs `upgrade head` then `downgrade base`
with `render_as_batch=True` from `env.py`. Every hidden test therefore checks
something SQLite can see (schema after upgrade, data across a migration,
downgrade to a specific revision, `compare_metadata` against `Base.metadata`)
or inspects the migration file source for a required pattern. Pick defects by
id, plant them so the PR reads as honest feature work, and plant one entry
from "Looks wrong but is fine" in the same PR.

## Do not plant

- Trivia (a linter's job, not a reviewer's): MG-12, MG-16, MG-17
- Internals (deeper than a generalist interview goes): MG-15

Everything else is the middle band a strong generalist is expected to reason about. Pick from it.

## Defects

### MG-01: NOT NULL column added without a server default
- Severity: Blocker
- Description: `op.add_column("orders", sa.Column("channel", sa.String(16), nullable=False))` has no `server_default`, so the migration fails on any table that already has rows (Postgres rejects the ADD COLUMN, SQLite raises "Cannot add a NOT NULL column with default value NULL").
- Planting: Feature "track sales channel per order". `0003_orders_channel.py` adds the column exactly as above; `models.py` gets `channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")`, which hides the problem in ORM-only tests because `Base.metadata.create_all` never runs the migration on populated tables.
- Hidden test: Upgrade to `0002`, insert one customer and one order with `text()`, then `command.upgrade(cfg, "head")` must succeed and the existing row must read `channel == "web"`, and `insp.get_columns("orders")` must show a non-null `default` for `channel`.

### MG-02: Non-concurrent index creation on `orders`
- Severity: Blocker
- Description: `op.create_index("ix_orders_status_created", "orders", ["status", "created_at"])` builds the index under a `SHARE` lock inside the `env.py` transaction, blocking every order write for the duration of the build on the largest table in the system.
- Planting: Feature "speed up `OrderRepository.list_by_status`". The migration copies the shape of `0002` verbatim. The honest fix is `postgresql_concurrently=True` inside `with op.get_context().autocommit_block():`, which is required because `run_migrations_online` wraps every revision in `context.begin_transaction()`.
- Hidden test: Static. Read the `0003` source and assert it contains both `postgresql_concurrently=True` and `autocommit_block(`. A second assertion upgrades head on SQLite and checks the index exists, proving the concurrent flag is a no-op there.

### MG-03: Downgrade drops the wrong column
- Severity: Blocker
- Description: `upgrade` adds `orders.notes`; `downgrade` runs `batch_op.drop_column("currency")`, so a rollback destroys the `currency` column and every order's currency value.
- Planting: Feature "free-text notes on orders". The author wrote the downgrade by editing a copy of another migration and left the column name from that copy. `tests/test_migrations.py` still passes because `downgrade base` ends with `0001` dropping the whole table.
- Hidden test: Upgrade head, insert an order with `currency = "EUR"` via `text()`, `command.downgrade(cfg, "0002")`, then assert `orders` has no `notes` column, still has `currency`, and the row still reads `"EUR"`.

### MG-04: Column renamed in one step while running code uses the old name
- Severity: Blocker
- Description: `batch_op.alter_column("idempotency_key", new_column_name="client_request_id")` renames the column in the same deploy that changes the model, so every pod still running the previous release fails `OrderRepository.by_idempotency_key` and `OrderRepository.add` with "column idempotency_key does not exist" until the rollout finishes, and the unique constraint `uq_orders_customer_idem` is silently rebuilt or lost depending on the dialect.
- Planting: Feature "rename idempotency_key to client_request_id to match the public API". One migration, one model edit, all call sites updated in the same PR. Looks tidy.
- Hidden test: Upgrade head, then a `text()` insert into `orders` that names `idempotency_key` must succeed (expand step keeps the old column, the contract step is a later revision), and `insp.get_unique_constraints("orders")` must still contain `uq_orders_customer_idem`.

### MG-05: Primary key type change rewrites `orders` and `order_items`
- Severity: Blocker
- Description: `alter_column("orders", "id", type_=sa.BigInteger())` plus the matching change on `order_items.order_id` rewrites both tables under an `ACCESS EXCLUSIVE` lock on Postgres, which is a full outage of order reads and writes for the duration.
- Planting: Feature "id headroom before we hit 2^31". `0003_bigint_ids.py` alters both columns inside `batch_alter_table`, which SQLite happily performs by recreating the table in milliseconds, so the test suite stays green.
- Hidden test: Upgrade head, then `insp.get_columns("orders")` shows `id` reflected as `INTEGER`, not `BIGINT`, and the same for `order_items.order_id`. The reference fix drops the change (Integer has years of headroom at current volume) and records the rewrite as a maintenance-window task.

### MG-06: Two heads from a copied `down_revision`
- Severity: Major
- Description: `0003` declares `down_revision = "0001"` because it was copied from `0002`, so the script directory has two heads and `alembic upgrade head` refuses to run with "Multiple head revisions are present".
- Planting: Feature "add `shipped_at` to orders". Everything in the file is right except the `down_revision` line and the `Revises:` docstring, both of which say `0001`. Easy to miss in a diff that only shows the new file.
- Hidden test: `ScriptDirectory.from_config(cfg).get_heads()` has length 1, and `command.upgrade(cfg, "head")` succeeds and leaves `ix_orders_customer_created` in place.

### MG-07: Empty downgrade
- Severity: Major
- Description: `def downgrade() -> None: pass` under an upgrade that adds `orders.shipped_at`, so the revision cannot be rolled back and a rollback deploy leaves the column behind with the previous model unaware of it.
- Planting: Feature "record shipment time". The author left the autogenerate placeholder body. `tests/test_migrations.py` does not notice because `downgrade base` reaches `0001`, which drops `orders` outright.
- Hidden test: Upgrade head, `command.downgrade(cfg, "0002")`, assert `shipped_at` is not in `insp.get_columns("orders")`, then `command.upgrade(cfg, "head")` again succeeds.

### MG-08: Data backfill mixed into the schema migration
- Severity: Major
- Description: After adding `order_items.line_total`, the same `upgrade` runs `op.execute("UPDATE order_items SET line_total = quantity * unit_price")` and then flips the column to NOT NULL, holding row locks on the whole `order_items` table in one transaction and turning a metadata change into a multi-minute rewrite that cannot be resumed if it fails halfway.
- Planting: Feature "precomputed line totals for reports". The migration reads well: add nullable, backfill, tighten. The backfill belongs in a batched script (or the column stays nullable and is filled lazily); the migration should only add the column.
- Hidden test: Static. The `0003` source contains no `UPDATE` statement and no `op.execute(`. Plus a behavioral check that upgrade head leaves `line_total` nullable so the migration does not depend on the backfill having run.

### MG-09: New foreign key without an index
- Severity: Major
- Description: `orders.discount_code_id` is added as `sa.ForeignKey("discount_codes.id")` with no index, so every `DELETE` or `UPDATE` on `discount_codes` scans `orders` to check the constraint, and the join from codes to orders in the reports router is a sequential scan.
- Planting: Feature "discount codes table". `0003_discount_codes.py` creates `discount_codes` (`id`, `code`, `percent_off`, `active`) and adds the FK column to `orders`. Postgres creates no index for FK columns automatically; the author assumed it does.
- Hidden test: Upgrade head, assert some entry in `insp.get_indexes("orders")` has `column_names[0] == "discount_code_id"`, and `compare_metadata` reports no `add_index` for `orders`.

### MG-10: Server default removed after the column is added
- Severity: Major
- Description: The migration adds `customers.marketing_opt_in` as `Boolean, nullable=False, server_default=sa.false()` and then immediately runs `alter_column(..., server_default=None)` "so the Python default is the single source of truth". After that, every writer that bypasses the ORM (the `text()` inserts in seed scripts, the batch job's JDBC writes, manual support inserts) fails with a NOT NULL violation.
- Planting: Feature "marketing opt-in flag". Model gets `default=False`; migration does the add-then-drop in two ops with a comment explaining the intent. Reads like discipline.
- Hidden test: Upgrade head, `text()` insert into `customers` that omits `marketing_opt_in` succeeds and reads back `0`, and `insp.get_columns("customers")` shows a non-null `default` for the column.

### MG-11: Column dropped while running code still reads it
- Severity: Major
- Description: The same migration that creates `order_discounts` also runs `batch_op.drop_column("discount_code")` on `orders`, but `OrderService.create` at `app/services/order_service.py:66` and the orders response model still write and read `discount_code`, so the previous release fails on every order create during the rollout and the new release loses the historical codes.
- Planting: Feature "multiple discount codes per order". PR adds the link table, migrates nothing, and drops the old column as cleanup. Expand and contract means the drop is a later revision after the code stops referencing the column.
- Hidden test: Upgrade head, `orders` still has a `discount_code` column and a `text()` insert that sets it succeeds; `order_discounts` exists with an index on `order_id`.

### MG-12: Index name in the migration differs from the model
- Severity: Minor
- Description: Migration creates `ix_orders_status`, model declares `Index("ix_orders_status_created", "status", "created_at")`, so `compare_metadata` reports a remove plus an add and the next `alembic revision --autogenerate` will drop and rebuild the index on production.
- Planting: Feature "index for `list_by_status`". The author renamed the index in `models.py` after a review comment and forgot the migration. Both names look plausible.
- Hidden test: Upgrade head, build a `MigrationContext` on the SQLite connection with `compare_type=True`, and assert no entry in `compare_metadata(ctx, Base.metadata)` mentions an index on `orders`.

### MG-13: Migration imports ORM models
- Severity: Minor
- Description: `from app.db.models import Order` inside `0003` and `op.bulk_insert(Order.__table__, ...)` or a `Session(bind=op.get_bind()).execute(select(Order))` ties the migration to whatever the model looks like at the time it runs, so it breaks the first time `Order` gains a column that this revision has not created yet.
- Planting: Feature "seed the default `discount_codes` rows". The migration imports the new `DiscountCode` model to insert three rows. Convenient and wrong; the fix is an inline `sa.table("discount_codes", sa.column("code"), ...)`.
- Hidden test: Static. No file under `app/db/alembic/versions/` contains `app.db.models`. Behavioral companion: upgrade head on SQLite still inserts the three seed rows.

### MG-14: Timestamp column without `timezone=True`
- Severity: Minor
- Description: `sa.Column("shipped_at", sa.DateTime(), nullable=True)` while the model says `DateTime(timezone=True)` and convention 7 requires aware UTC. On Postgres the column becomes `timestamp without time zone`, values written through `ensure_utc` lose their offset, and `compare_metadata` reports a type diff forever.
- Planting: Feature "record shipment time". Every other timestamp in `0001` uses `sa.DateTime(timezone=True)`; the new column omits it. One missing keyword argument.
- Hidden test: Static. The `0003` source declares `shipped_at` with `timezone=True`. SQLite reflects both as `DATETIME`, so this one cannot be caught by inspection.

### MG-15: Status converted to a native enum type
- Severity: Minor
- Description: `alter_column("orders", "status", type_=sa.Enum(OrderStatus, name="order_status"))` creates a Postgres enum type. Adding a status later needs `ALTER TYPE ... ADD VALUE`, which cannot run inside the `env.py` transaction, and the downgrade converts the column back to `String(32)` but never drops the type, so downgrade then upgrade fails with "type order_status already exists".
- Planting: Feature "enforce valid order statuses at the database". Model changes `status` to `Mapped[OrderStatus]` with `sa.Enum`. SQLite creates no type, so the suite is green.
- Hidden test: Static. The `0003` `downgrade` source contains `DROP TYPE` (or `sa.Enum(...).drop(`), or the column stays `String(32)` with a CHECK constraint listing every `OrderStatus` member; plus upgrade, downgrade to `0002`, upgrade again succeeds on SQLite.

### MG-16: Migration file name breaks the naming pattern
- Severity: Nit
- Description: The new revision file is `add_notes.py` while the existing files are `0001_initial.py` and `0002_orders_customer_created_index.py`; directory listing no longer sorts by revision order.
- Planting: Any feature above. Alembic does not care about the file name, so nothing fails.
- Hidden test: Static. Every file in `app/db/alembic/versions/` other than `__init__` matches `^\d{4}_[a-z0-9_]+\.py$`.

### MG-17: Docstring header disagrees with the revision variables
- Severity: Nit
- Description: The module docstring says `Revision ID: 0002` and `Revises: 0001` (left over from the copy) while `revision = "0003"` and `down_revision = "0002"` are correct, and `import sqlalchemy as sa` is unused (`pyproject.toml` excludes `versions/` from ruff, so nobody is warned).
- Planting: Any feature above. The chain is correct; only the human-readable header lies.
- Hidden test: Static. For each versions file, the `Revision ID:` and `Revises:` docstring lines equal `revision` and `down_revision`, and the module imports only names it uses.

## Looks wrong but is fine

### MG-CLEAN-01: `batch_alter_table` on a Postgres deployment
- Pattern: `with op.batch_alter_table("orders") as batch_op: batch_op.add_column(...)` in a migration that will run on Postgres.
- Why it is fine: `env.py` sets `render_as_batch=True` so migrations work on SQLite, where `ALTER` support is limited. On Postgres `batch_alter_table` with the default `recreate="auto"` emits plain `ALTER TABLE` statements and never copies the table.
- What a reviewer might wrongly say: "This recreates the whole `orders` table in production; use `op.add_column` directly."

### MG-CLEAN-02: NOT NULL with a constant `server_default`
- Pattern: `sa.Column("channel", sa.String(16), nullable=False, server_default="web")` added to `orders`.
- Why it is fine: Postgres 11 and later stores a constant default in the catalog and does not rewrite the table; existing rows read the default lazily. This matches how `0001` declared `status`, `currency`, and the money columns.
- What a reviewer might wrongly say: "Adding a NOT NULL column with a default rewrites every row in `orders` and will lock the table for minutes."

### MG-CLEAN-03: Inline `sa.table()` instead of the ORM model
- Pattern: `discount_codes = sa.table("discount_codes", sa.column("code", sa.String), sa.column("percent_off", sa.Integer))` followed by `op.bulk_insert(discount_codes, [...])` in the migration.
- Why it is fine: A migration must describe the schema as of its own revision. Importing `app.db.models` would bind it to the current model, which drifts over time (see MG-13). The lightweight table duplicates a few column names on purpose.
- What a reviewer might wrongly say: "Duplicated schema; import `DiscountCode` from `models.py` so there is one source of truth."

### MG-CLEAN-04: Concurrent index inside `autocommit_block`
- Pattern: `with op.get_context().autocommit_block(): op.create_index(..., postgresql_concurrently=True)` in a migration that also runs on SQLite.
- Why it is fine: `CREATE INDEX CONCURRENTLY` cannot run inside a transaction, and `run_migrations_online` opens one for every revision, so `autocommit_block` is the only correct way to build an index on a large table without blocking writes. SQLite ignores the `postgresql_` prefixed option and builds the index normally.
- What a reviewer might wrongly say: "This breaks the migration transaction, so a failure leaves a half-applied revision," or "`postgresql_concurrently` will error on SQLite."
