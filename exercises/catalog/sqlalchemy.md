# SQLAlchemy defect catalog

Each entry is one plantable defect for a feature PR against `app/db/` and its callers, with a planting recipe tied to real files in this repository and a description of the hidden test under `solutions_tests/` that fails on the defect and passes on the fix. `/exercise` picks entries by severity mix, plants them as honest-looking feature work, and plants exactly one pattern from "Looks wrong but is fine" as the false-positive trap. Severities follow the scale in `CLAUDE.md`. Every hidden test runs on SQLite in memory with `StaticPool` (see `conftest.py`), so one connection is shared by every session in a test: uncommitted writes are visible across sessions, and tests that need to prove "not committed" do the work, call `db.rollback()`, and then assert the row is gone.

## Defects

### SA-01: SQL injection via f-string in customer search
- Severity: Blocker
- Description: A new search query interpolates user input into raw SQL instead of binding it, so a crafted email fragment changes the WHERE clause.
- Planting: Add `CustomerRepository.search(self, fragment: str)` in `app/db/repositories.py` backing a new admin endpoint `GET /customers?q=` in `app/api/routers/customers.py`. The "mistake" is `self.session.execute(text(f"SELECT id FROM customers WHERE email LIKE '%{fragment}%'"))` with a comment that `LIKE` needs the wildcards inlined.
- Hidden test: With `db` and `seeded`, add a second customer, then call `search("' OR '1'='1")` and assert it returns zero rows (defect returns both). A second assertion attaches `before_cursor_execute` to `engine`, calls `search("ada")`, and asserts the literal `ada` appears in `parameters` and not in `statement`.

### SA-02: Stock decrement committed before the order insert
- Severity: Blocker
- Description: `OrderService.create` commits after adjusting `Product.stock` and before adding the order, so a failed insert leaves stock permanently reduced with no order.
- Planting: A "reserve stock early" feature in `app/services/order_service.py`: after the `products[i.sku].stock -= i.quantity` loop, add `self.session.commit()` with a comment about releasing row locks before pricing. The `begin_nested()` block and `IntegrityError` handler stay as they are.
- Hidden test: With `db` and `seeded`, build an `OrderService` and call `create` twice with the same `idempotency_key` but force the second insert to fail (monkeypatch `OrderRepository.add` to raise `IntegrityError`, then `db.rollback()`). Assert `WIDGET.stock` after `db.refresh` equals `100 - quantity` once, not twice.

### SA-03: Bulk cancel bypasses the state machine and stock restore
- Severity: Blocker
- Description: A Core `update(Order)` sets `status="cancelled"` for a list of ids, skipping `transition()` and the per-item `product.stock += quantity` in `OrderService.cancel`.
- Planting: New admin endpoint `POST /orders/bulk-cancel` in `app/api/routers/orders.py` calling a new `OrderRepository.cancel_many(ids)`:
  ```python
  stmt = update(Order).where(Order.id.in_(ids)).values(status=OrderStatus.CANCELLED)
  self.session.execute(stmt)
  ```
  The PR description calls it "one statement instead of N service calls".
- Hidden test: With `db` and `seeded`, create a paid order for 2 `WIDGET` through `OrderService.create` plus `mark_paid`, then a shipped order. Call `cancel_many([paid.id, shipped.id])` and assert `WIDGET.stock` is back to 100 and the shipped order raises `InvalidTransition` (or is left `shipped`), not silently canceled.

### SA-04: Float column for a money value
- Severity: Blocker
- Description: A new `shipping_fee` column is declared `Float` instead of `Numeric(12, 2)`, so totals round unpredictably and `Money.of` rejects the value.
- Planting: In `app/db/models.py`, add `shipping_fee: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)` on `Order`, plus the Alembic migration `0003_orders_shipping_fee.py` with `sa.Float()`. `OrderService.create` sets `order.shipping_fee = float(q.shipping.amount)`.
- Hidden test: With `db` and `seeded`, create an order with `shipping_fee=Decimal("0.10")`, commit, `db.expire(order)`, and assert `isinstance(order.shipping_fee, Decimal)` and that `Money.of(order.shipping_fee)` does not raise.

### SA-05: One default address per customer enforced only by a lookup
- Severity: Blocker
- Description: A new `customer_addresses` table with an `is_default` flag has no unique constraint, so two concurrent inserts leave a customer with two defaults.
- Planting: New model `CustomerAddress` in `app/db/models.py` and `CustomerRepository.set_default_address` that does `select(...).where(is_default == True)` then either updates or inserts. No `UniqueConstraint("customer_id", "is_default")` or partial index in the model or in migration `0003`.
- Hidden test: With `db` and `seeded`, insert two `CustomerAddress(customer_id=c.id, is_default=True)` rows directly and `db.commit()`. Assert `IntegrityError` is raised (the fix adds the constraint and catches the race in the repository).

### SA-06: Module-level session shared across worker threads
- Severity: Blocker
- Description: A worker handler module creates one `Session` at import time and every handler uses it from `asyncio.to_thread`, so concurrent tasks share one non-thread-safe session and one transaction.
- Planting: New `app/async_tasks/handlers.py`:
  ```python
  _session = get_session_factory()()


  def mark_paid(payload):
      OrderService(_session, PricingService(), notifications).mark_paid(payload["order_id"])
      _session.commit()
  ```
  registered on `QueueWorker` in a new `app/async_tasks/main.py`.
- Hidden test: Monkeypatch `get_session_factory` in `app.async_tasks.handlers` to the `session_factory` fixture, reload the module, call `mark_paid` twice, and assert `sqlalchemy.orm.object_session(order)` is not a module global (`handlers._session` does not exist) and that a counting `sessionmaker` wrapper saw one new session per call, each closed afterward.

### SA-07: N+1 on `item.product` in the top-products report
- Severity: Major
- Description: A report loops over orders and items and touches `item.product.name`, issuing one SELECT per item because `OrderItem.product` is a default lazy relationship.
- Planting: New `GET /reports/orders/top-products` in `app/api/routers/reports.py` using `OrderRepository.created_between` then `for o in rows: for i in o.items: totals[i.product.name] += i.quantity`. No `selectinload(Order.items).selectinload(OrderItem.product)`.
- Hidden test: With `db` and `seeded`, create 5 orders with 2 items each, then attach a `before_cursor_execute` listener to `engine` that appends each statement to a list, call the report function, and assert the number of SELECT statements is at most 3 (orders, items, products). The defect issues 13.

### SA-08: New `shipped_at` query has no index
- Severity: Major
- Description: A carrier reconciliation query filters `orders` by a new `shipped_at` range, and neither the model nor the migration adds an index, so the query is a full scan.
- Planting: Add `shipped_at: Mapped[datetime | None]` to `Order` in `app/db/models.py`, migration `0003_orders_shipped_at.py` with only `op.add_column`, and `OrderRepository.shipped_between(start, end)`. The existing `ix_orders_customer_created` does not cover it.
- Hidden test: Like `tests/test_migrations.py`, run `alembic upgrade head` against a tmp SQLite file and assert some index on `orders` has `column_names == ["shipped_at"]` (or starts with it). The `engine` fixture's `create_all` would mask a migration-only omission, so the test must go through Alembic.

### SA-09: Paginated status list ordered without a tiebreaker
- Severity: Major
- Description: A new paginated query orders by `created_at` only; `server_default=func.now()` is second-granular, so pages skip and repeat orders created in the same second.
- Planting: Extend `OrderRepository.list_by_status` with `offset` for a new admin `GET /orders?status=` and change the ordering to `.order_by(Order.created_at.desc())`, dropping `Order.id`. The PR says "newest first".
- Hidden test: SQLite returns ties in rowid order, so page contents look right on the defect. The test captures the compiled statement with `before_cursor_execute` while calling `list_by_status(OrderStatus.PAID, limit=10, offset=0)` and asserts the `ORDER BY` clause references `orders.id`.

### SA-10: Helper opens a session per call and never closes it
- Severity: Major
- Description: A reconciliation helper calls `get_session_factory()()` on every invocation without `close()` or `session_scope()`, leaking a pooled connection per call until the pool is exhausted.
- Planting: New `app/services/stock_service.py` with `reconcile_stock(order_id)` used by a worker handler:
  ```python
  session = get_session_factory()()
  order = OrderRepository(session).get(order_id)
  ...
  session.commit()
  return summary
  ```
- Hidden test: Monkeypatch `get_session_factory` in `app.services.stock_service` with a wrapper around the `session_factory` fixture that records every session created, call `reconcile_stock` three times, and assert every recorded session has `.is_active is False` after `close()` or that `session.get_bind().pool.checkedout() == 0` (the fixture pool reports one shared connection, so use the recorded-session check).

### SA-11: Missing rollback after a caught IntegrityError
- Severity: Major
- Description: A batch import catches `IntegrityError` per row and continues, but the session is now in a failed transaction, so the next flush raises `PendingRollbackError` and the whole batch dies.
- Planting: New `CustomerRepository.import_many(rows)` in `app/db/repositories.py` (or a service function) that does `add` and `flush` per row inside `try/except IntegrityError: skipped.append(row.email)` with no `begin_nested()` savepoint and no `rollback()`.
- Hidden test: With `db` and `seeded`, call `import_many` with three rows where the first duplicates `ada@example.com`. Assert the call returns without raising, the two new customers are queryable with `by_email`, and the skipped list is exactly `["ada@example.com"]`.

### SA-12: Check-then-insert race leaks a 500
- Severity: Major
- Description: A new endpoint checks `by_email` then inserts; when two requests race, the loser hits the unique constraint and the `IntegrityError` propagates as a 500 instead of a 409.
- Planting: New `POST /customers/register` in `app/api/routers/customers.py` (self-service signup with an API key) copying the `create_customer` shape: `if repo.by_email(...)` then `repo.add(...)`, with no `except IntegrityError`.
- Hidden test: With `client` and `seeded`, monkeypatch `CustomerRepository.by_email` to return `None` (a simulated lost race) and POST the existing email. Assert the response status is 409, not 500, and that a subsequent `GET /customers/me` on the same client still works.

### SA-13: Detached instance accessed after the session closes
- Severity: Major
- Description: A worker handler returns an `Order` from inside `session_scope()` and reads `order.customer.email` after the block, which raises `DetachedInstanceError` because the relationship was never loaded.
- Planting: New `app/async_tasks/handlers.py`:
  ```python
  with session_scope() as session:
      order = OrderRepository(session).get(payload["order_id"])
  notifications.order_shipped(order.customer.email, order.id)
  ```
- Hidden test: Monkeypatch `get_session_factory` in `app.db.session` to the `session_factory` fixture (so `session_scope` uses the test engine), create an order through `db`, call the handler with `{"order_id": order.id}`, and assert it does not raise and the in-memory sender recorded one `order_shipped` message.

### SA-14: New session factory drops `expire_on_commit=False`
- Severity: Minor
- Description: A script builds `sessionmaker(bind=get_engine())` with the default `expire_on_commit=True`, so every attribute read after each per-row commit re-selects the row.
- Planting: New `app/services/backfill.py` for a "backfill shipped_at" job that creates its own factory, commits once per order in a loop, then builds a summary from `order.id` and `order.total` after each commit. Works, but issues one extra SELECT per order.
- Hidden test: Monkeypatch the factory name in `app.services.backfill` to the `session_factory` fixture, seed 5 orders, count statements with `before_cursor_execute`, and assert the SELECT count is at most 2 (one list query, one optional recheck). The defect issues 5 extra reloads.

### SA-15: Repository method commits
- Severity: Minor
- Description: `OrderRepository.mark_shipped` calls `self.session.commit()`, breaking "repositories never commit" and making the request dependency's own commit and rollback meaningless.
- Planting: New `OrderRepository.mark_shipped(order_id, shipped_at)` in `app/db/repositories.py` used by `OrderService.ship`, ending with `self.session.commit()` "so the timestamp is durable before the notification goes out".
- Hidden test: With `db` and `seeded`, create an order, call `mark_shipped`, then `db.rollback()`, `db.expire_all()`, and assert `shipped_at` is still `None`. On the defect the commit already persisted it.

### SA-16: `in_` with an empty list returns nothing instead of everything
- Severity: Minor
- Description: A new status filter treats an empty list as "no filter" in the API but passes it straight to `Order.status.in_([])`, which compiles to an always-false predicate, so the default admin listing is empty.
- Planting: `OrderRepository.list_by_statuses(statuses: list[OrderStatus], limit, offset)` with `.where(Order.status.in_(statuses))` and `GET /orders?status=` in `app/api/routers/orders.py` defaulting `status` to `[]`. The guard in `ProductRepository.by_skus` shows the house pattern.
- Hidden test: With `db` and `seeded`, add two orders with different statuses, call `list_by_statuses([])` and assert it returns both; call with `[OrderStatus.PAID]` and assert one.

### SA-17: Counting by loading every row
- Severity: Minor
- Description: A new count method runs `select(Order)` and returns `len(...all())`, loading every order and its `selectin` items into memory to produce one integer.
- Planting: `OrderRepository.count_for_customer(customer_id)` in `app/db/repositories.py` used to add a `total` field to `Page[OrderOut]` in `list_orders`: `return len(self.session.scalars(select(Order).where(...)).all())` instead of `select(func.count(Order.id))`.
- Hidden test: With `db` and `seeded`, create 4 orders with items, attach `before_cursor_execute`, call `count_for_customer`, and assert exactly one statement ran and it contains `count(`. The defect runs two (orders, then items via selectin).

### SA-18: `refresh()` after every flush in a loop
- Severity: Minor
- Description: A loop that adjusts stock calls `session.flush()` then `session.refresh(product)` per item, issuing a SELECT per row for data the identity map already holds.
- Planting: In the `OrderService.create` stock loop in `app/services/order_service.py`, replace the plain decrement with `product.stock -= qty; self.session.flush(); self.session.refresh(product)` "to read back the server value". Nothing is server-computed here.
- Hidden test: With `db` and `seeded`, create an order with 3 distinct SKUs while counting statements; assert no SELECT on `products` runs after the initial `by_skus` load (defect adds 3).

### SA-19: Legacy `session.query()` in new code
- Severity: Nit
- Description: A new repository method uses the 1.x `self.session.query(Order).filter(...)` style in a codebase that is uniformly `select()` and `session.scalars()`.
- Planting: `OrderRepository.latest_for_customer(customer_id)` written as `self.session.query(Order).filter(Order.customer_id == customer_id).order_by(Order.id.desc()).first()`.
- Hidden test: None runnable; graded by review only. The reference fix rewrites it as `select(Order)...limit(1)` with `session.scalar`.

### SA-20: Status literal instead of the enum
- Severity: Nit
- Description: A new query compares `Order.status == "paid"` instead of `OrderStatus.PAID`, so a rename of the enum value silently breaks the filter.
- Planting: In a new `OrderRepository.paid_between(start, end)` or in the report from SA-07, filter with the string `"paid"` while the rest of the file uses `OrderStatus`.
- Hidden test: None runnable; graded by review only. The reference fix substitutes `OrderStatus.PAID`.

### SA-21: Docstring says returns `None`, code raises
- Severity: Nit
- Description: A new getter's docstring promises `None` when missing, but the body raises `NotFound`, so the next caller writes a dead `if row is None` check.
- Planting: `OrderRepository.get_shipped(order_id)` with docstring "Return the order, or None if it has not shipped." followed by `raise NotFound("order", order_id)`. Optionally leave an unused `from sqlalchemy import text` import from an earlier draft.
- Hidden test: None runnable; graded by review only. The reference fix aligns the docstring with the raise.

## Looks wrong but is fine

### SA-CLEAN-01: Looping over `order.items` inside a loop over orders
- Pattern: `for order in rows: for item in order.items: ...` in a report or service method, with no `selectinload` on the query.
- Why it is fine: `Order.items` is declared `lazy="selectin"` in `app/db/models.py`, so SQLAlchemy loads items for the whole batch in one extra SELECT after the orders query. The N+1 in this codebase lives on `OrderItem.product` and `Order.customer`, which are default lazy.
- What a reviewer might wrongly say: "This is an N+1, add `selectinload(Order.items)` or a join."

### SA-CLEAN-02: `expire_on_commit=False` on the session factory
- Pattern: `sessionmaker(bind=get_engine(), expire_on_commit=False)` in `app/db/session.py`, and the same in `conftest.py`.
- Why it is fine: Sessions are per request (`get_db` in `app/api/deps.py`) or per `session_scope()` block, and the commit happens after the handler has returned its ORM rows to the response model. Expiring on commit would make FastAPI's serialization re-select every row or fail on a closed session. Nothing long-lived reads stale state.
- What a reviewer might wrongly say: "Disabling expiry means handlers return stale data after commit; remove this flag."

### SA-CLEAN-03: `flush()` inside a repository `add`
- Pattern: `self.session.add(order); self.session.flush(); return order` in `OrderRepository.add` and the other `add` methods.
- Why it is fine: `flush` sends the INSERT inside the caller's open transaction so `order.id` is populated and constraint violations surface at the call site (which `OrderService.create` relies on for its `IntegrityError` handler). It does not commit; the caller still owns the transaction boundary per convention 8.
- What a reviewer might wrongly say: "Repositories must not commit, remove the flush" or "flush here is a hidden commit."

### SA-CLEAN-04: Raw SQL through `text()` with named parameters
- Pattern: `self.session.execute(text("SELECT ... WHERE customer_id = :cid AND created_at >= :start"), {"cid": customer_id, "start": start})`.
- Why it is fine: Convention 1 explicitly allows `text()` with named bound parameters. The values travel as DBAPI parameters, never as interpolated strings, so no user input reaches the SQL text. The prohibited shapes are f-strings, `%` formatting, and `.format`.
- What a reviewer might wrongly say: "Raw SQL is banned in this codebase, this is an injection risk, rewrite as `select()`."
