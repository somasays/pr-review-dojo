# FastAPI defect catalog

Defects for exercises in the `fastapi` domain, all planted in `app/api/` (`main.py`, `deps.py`,
`schemas.py`, `routers/*.py`) and occasionally in the repository methods an endpoint needs.
`/exercise` and `/seed` pick a mix by severity, wrap them in a plausible feature PR, and copy
the matching hidden test descriptions into `solutions_tests/` on the solution branch. Every
entry names the file and function to touch, the feature that would naturally touch it, and what
the hidden test asserts. The final section lists code that looks suspicious but is correct in
this codebase; one of those is planted in every exercise as the false-positive trap. Severities
follow the scale in `CLAUDE.md`.

## Do not plant

- Trivia (a linter's job, not a reviewer's): FA-19, FA-20, FA-21

Everything else is the middle band a strong generalist is expected to reason about. Pick from it.

## Defects

### FA-01: On-behalf-of header honored for non-admin keys
- Severity: Blocker
- Description: `get_principal` trusts an `X-On-Behalf-Of` header before checking whether the key
  is an admin key, so any customer can act as any other customer.
- Planting: Feature "admin impersonation for support". In `app/api/deps.py` `get_principal`, add
  `x_on_behalf_of: Annotated[int | None, Header()] = None` and, right after the missing-key
  check, `if x_on_behalf_of is not None: return Principal(customer_id=x_on_behalf_of,
  is_admin=False)`. The admin membership check comes after it, so the guard is never reached for
  customer keys.
- Hidden test: With `client` and `db`, add a second `Customer` (`bob@example.com`, no key) and
  commit. `GET /customers/me` with `X-API-Key: CUSTOMER_KEY` and `X-On-Behalf-Of: <bob id>`.
  Assert the status is 403 (or the body email is `ada@example.com`); on the defect the body is
  Bob's record.

### FA-02: Admin customer detail leaks the API key hash
- Severity: Blocker
- Description: A new `CustomerDetail` response model includes `api_key_hash`, exposing
  credential material to every admin caller and to logs.
- Planting: Feature "admin can look up a customer by id". In `app/api/schemas.py` add
  `CustomerDetail(CustomerOut)` with `api_key_hash: str | None` ("useful for support to see
  whether a key is issued"), and in `app/api/routers/customers.py` add `GET
  /customers/{customer_id}` returning `CustomerRepository(db).get(customer_id)` through it.
- Hidden test: With `client` and `seeded`, `GET /customers/<ada id>` with `ADMIN_KEY`. Assert
  200 and `"api_key_hash" not in r.json()`. A `has_api_key: bool` field is the accepted fix.

### FA-03: Cancel no longer checks order ownership
- Severity: Blocker
- Description: `cancel_order` drops the `get_for_customer` ownership check, so a customer can
  cancel another customer's order by id.
- Planting: Feature "cancel returns the restocked quantities". While reworking `cancel_order` in
  `app/api/routers/orders.py`, the pre-check `OrderRepository(db).get_for_customer(order_id,
  principal.customer)` is replaced by `OrderRepository(db).get(order_id)` to "avoid a redundant
  query" because the result was unused, and `service.cancel(order_id)` runs for any principal.
- Hidden test: With `client` and `db`, create a second customer and an `Order` for them in
  `pending_payment` via `OrderRepository.add`, commit. `POST /orders/<that id>/cancel` with
  `CUSTOMER_KEY`. Assert 404 and that `db.get(Order, id).status` is still `pending_payment`
  after `db.expire_all()`.

### FA-04: Principal cached process-wide by raw key
- Severity: Blocker
- Description: `get_principal` memoizes `Principal` per API key in a module-level dict, so a
  revoked or rotated key keeps working until restart.
- Planting: Feature "cut the auth query per request". In `app/api/deps.py` add
  `_principal_cache: dict[str, Principal] = {}`; `get_principal` returns the cached entry when
  present and stores every successful lookup. Reads plausibly as "keys rarely change".
- Hidden test: With `client` and `db`, `GET /customers/me` with `CUSTOMER_KEY` is 200. Then set
  `seeded["customer"].api_key_hash = None`, commit, repeat the request. Assert 401; on the
  defect it is 200.

### FA-05: `get_db` rewritten as a context manager and stops committing
- Severity: Blocker
- Description: `get_db` becomes `with get_session_factory()() as session: yield session`, which
  closes (and therefore rolls back) but never commits, so every write through the API is
  silently lost.
- Planting: Feature "tidy the session dependency". The try/commit/rollback/finally block in
  `app/api/deps.py` `get_db` is collapsed to the two-line `with` form. All existing API tests
  still pass because the `client` fixture overrides `get_db`.
- Hidden test: With `session_factory`, `seeded`, and `monkeypatch`, set
  `app.api.deps.get_session_factory` to `lambda: session_factory`, build `create_app()` without
  the override, and `POST /orders` with `CUSTOMER_KEY` inside a `TestClient`. Assert 201, then
  open a fresh session from `session_factory` and assert `session.get(Order, body["id"])` is not
  None.

### FA-06: Restock moved to a background task after the session is closed
- Severity: Blocker
- Description: Cancel returns immediately and restocks in `BackgroundTasks` using the request
  session, which `get_db` has already committed and closed, so stock is never restored.
- Planting: Feature "faster cancel". In `app/api/routers/orders.py` `cancel_order` takes
  `background: BackgroundTasks`, calls `service.cancel(order_id)` with restocking removed from
  `OrderService.cancel`, and adds `background.add_task(_restock, db, order_id)` where `_restock`
  loops `item.product.stock += item.quantity` and calls `db.flush()`.
- Hidden test: With `client` and `db`, create an order for 2 `WIDGET` (stock 100 to 98), `POST
  /orders/{id}/cancel` with `CUSTOMER_KEY`, assert 200. `db.expire_all()` and assert
  `seeded["products"]["WIDGET"].stock == 100`; on the defect it stays 98 or the task raises
  `DetachedInstanceError`.

### FA-07: `async def` handler runs sync database and notification work on the event loop
- Severity: Major
- Description: `pay_order` is declared `async def` but calls the synchronous
  `service.mark_paid`, which does ORM queries and retried sends, blocking every other request
  while it runs.
- Planting: Feature "add request timing to pay". The handler becomes `async def pay_order(...)`
  so it can `await` a small timing helper, and the body still calls
  `service.mark_paid(order_id)` directly. Violates convention 5.
- Hidden test: Build `create_app()` and find the route whose path is `/orders/{order_id}/pay`.
  Assert `inspect.iscoroutinefunction(route.endpoint)` is False, or that it is a coroutine whose
  body goes through `asyncio.to_thread`. A slower variant patches `_sender.send` with a 0.5 s
  sleep and asserts a concurrent `GET /health` from a thread returns in under 0.3 s.

### FA-08: Customer email per order triggers N+1 in list
- Severity: Major
- Description: `OrderOut` gains `customer_email` read from `o.customer.email`, and
  `Order.customer` is lazy `select`, so listing 200 orders issues 200 extra queries.
- Planting: Feature "show customer email in the admin order list". Add `customer_email: str` to
  `OrderOut` in `app/api/schemas.py` (computed via a `@field_validator` or a property on the
  row), and leave `OrderRepository.list_for_customer` untouched. Fix is
  `selectinload(Order.customer)` or `joinedload` in the repository.
- Hidden test: With `client`, `session_factory`, and `seeded`, register `before_cursor_execute`
  on `session_factory.kw["bind"]` to count statements, create 10 orders with distinct
  idempotency keys, reset the counter, `GET /orders` with `CUSTOMER_KEY`. Assert the count is at
  most 4 (principal lookup, orders, items selectin, customer load); on the defect it is at least
  13.

### FA-09: Offset dropped from the repository call
- Severity: Major
- Description: `list_orders` passes `limit` but not `offset` to `list_for_customer`, so every
  page returns the first page.
- Planting: Feature "status filter on the order list". The call in `app/api/routers/orders.py`
  `list_orders` is rewritten as `repo.list_for_customer(principal.customer,
  status=status_filter, limit=page.limit)` and `offset=page.offset` is lost in the edit. The
  response still echoes the requested offset.
- Hidden test: With `client`, create three orders, then `GET /orders?limit=2&offset=0` and `GET
  /orders?limit=2&offset=2` with `CUSTOMER_KEY`. Assert the second page has one item and its id
  is not in the first page.

### FA-10: New deliver endpoint does not map `InvalidTransition`
- Severity: Major
- Description: `POST /orders/{id}/deliver` calls `service.deliver` without catching
  `InvalidTransition`, so delivering an unpaid order returns 500 instead of 409.
- Planting: Feature "deliver endpoint for the warehouse". Copy `ship_order` in
  `app/api/routers/orders.py` but keep only the `except NotFound` branch. `OrderService.deliver`
  already exists and raises through `transition()`.
- Hidden test: With `client`, create an order (`pending_payment`) and `POST
  /orders/{id}/deliver` with `ADMIN_KEY`. Assert 409 and `"cannot move order"` in the detail; on
  the defect the `TestClient` raises `InvalidTransition`.

### FA-11: `/orders/summary` shadowed by `/orders/{order_id}`
- Severity: Major
- Description: The new static route is registered after the parameterized route, so `GET
  /orders/summary` is routed to `get_order` and fails with 422 on `order_id`.
- Planting: Feature "per-customer order summary". Append `@router.get("/summary",
  response_model=OrderSummary)` at the bottom of `app/api/routers/orders.py`, after `get_order`.
  Tests written with `client.get("/orders/summary")` would catch it, so the PR ships without
  one.
- Hidden test: With `client`, `GET /orders/summary` with `CUSTOMER_KEY`. Assert 200 and the body
  has `count` and `total`; on the defect it is 422 with `order_id` in the error location.

### FA-12: Region update skips the format validator
- Severity: Major
- Description: `CustomerUpdate.region` is a bare `str`, so `PATCH /customers/me` stores values
  like `california` that `PricingService.quote` cannot map to a tax rate, and every later order
  for that customer fails.
- Planting: Feature "customers can update their profile". Add `CustomerUpdate` to
  `app/api/schemas.py` with `name: str | None` and `region: str | None = None` without the
  `pattern=r"^[A-Z]{2}(-[A-Z]{2})?$"` that `CustomerCreate` carries, and a `PATCH /customers/me`
  handler in `app/api/routers/customers.py` that assigns the fields.
- Hidden test: With `client`, `PATCH /customers/me` with body `{"region": "california"}` and
  `CUSTOMER_KEY`. Assert 422; on the defect it is 200 and a following `POST /orders` fails.

### FA-13: Page size cap removed
- Severity: Major
- Description: `get_pagination` returns the raw `limit`, ignoring `settings.page_size_max`, so
  `?limit=1000000` dumps a whole table per request.
- Planting: Feature "make pagination defaults configurable". `get_pagination` in
  `app/api/deps.py` is rewritten to read a default from settings and returns
  `Pagination(limit=limit, offset=offset)`, dropping the `min(limit, settings.page_size_max)`.
  `Query(ge=1)` stays, so it looks validated.
- Hidden test: With `client` and `session_factory`, capture statements with
  `before_cursor_execute` and `GET /orders?limit=100000` with `CUSTOMER_KEY`. Assert the
  captured orders SELECT carries a limit parameter of 200; on the defect it is 100000.

### FA-14: Another customer's order returns 403 instead of 404
- Severity: Minor
- Description: `get_order` distinguishes "exists but not yours" from "does not exist", turning
  the endpoint into an order-id existence oracle.
- Planting: Feature "clearer error messages". In `app/api/routers/orders.py` `get_order`, the
  customer branch becomes `order = repo.get(order_id)` followed by `if order.customer_id !=
  principal.customer: raise HTTPException(403, "not your order")`.
- Hidden test: With `client` and `db`, create a second customer with an order, `GET
  /orders/<that id>` with `CUSTOMER_KEY`. Assert 404, matching the existing behavior tested in
  `tests/test_api_orders.py`.

### FA-15: `days` accepts zero and negatives
- Severity: Minor
- Description: `recent_total` adds an upper bound on `days` but no lower bound, so `?days=-3`
  produces `start > end` and a silent empty report instead of 422.
- Planting: Feature "cap report window". In `app/api/routers/reports.py` `recent_total`, change
  to `days: Annotated[int, Query(le=365)] = 7` and forget `ge=1`.
- Hidden test: With `client`, `GET /reports/orders/recent-total?days=-3` with `ADMIN_KEY`.
  Assert 422; on the defect it is 200 with `orders: 0`.

### FA-16: Status filter typed as free text
- Severity: Minor
- Description: The new `status` query parameter is a `str | None`, so a typo like
  `?status=payed` returns an empty page instead of a validation error.
- Planting: Feature "status filter on the order list" (pairs with FA-09). In
  `app/api/routers/orders.py` `list_orders` add `status_filter: Annotated[str | None,
  Query(alias="status")] = None` and pass it through to a repository `where(Order.status ==
  status)`. Should be `OrderStatus | None`.
- Hidden test: With `client`, `GET /orders?status=payed` with `CUSTOMER_KEY`. Assert 422; on the
  defect it is 200 with `items: []`. Also assert `?status=pending_payment` returns the created
  order.

### FA-17: Region listing has no `ORDER BY`
- Severity: Minor
- Description: `CustomerRepository.list_by_region` applies `limit` and `offset` without an
  `order_by`, so pages overlap or skip rows once the planner changes its scan order.
- Planting: Feature "admin lists customers by region". Add `list_by_region(region, limit,
  offset)` to `app/db/repositories.py` as `select(Customer).where(Customer.region ==
  region).limit(limit).offset(offset)` and call it from `list_customers` in
  `app/api/routers/customers.py` when `?region=` is present.
- Hidden test: With `client` and `session_factory`, capture statements and `GET
  /customers?region=US-CA` with `ADMIN_KEY`. Assert the captured customers SELECT contains
  `ORDER BY`. The existing `CustomerRepository.list` is the pattern to copy.

### FA-18: Handler commits the session itself
- Severity: Minor
- Description: The new `PATCH /customers/me` calls `db.commit()` inside the handler, taking the
  transaction boundary away from `get_db` and making the surrounding handler code non-atomic.
- Planting: Feature "customers can update their profile" (pairs with FA-12). In
  `app/api/routers/customers.py` `update_me`, after assigning fields, add `db.commit()` "so the
  response reflects the saved row". Breaks convention 8.
- Hidden test: With `client` and `monkeypatch`, wrap `sqlalchemy.orm.Session.commit` to count
  calls, `PATCH /customers/me` with a valid name. Assert exactly one commit for the request (the
  dependency's); on the defect there are two.

### FA-19: Unused import left behind
- Severity: Nit
- Description: `Query` is imported in `app/api/routers/orders.py` but never used after the
  status filter moved to `deps.py`.
- Planting: Any feature touching `orders.py` imports `from fastapi import APIRouter,
  HTTPException, Query, status` and then the code that used `Query` is refactored away.
- Hidden test: None. `uv run ruff check .` reports F401 on the fix branch, which the
  no-regressions section already scores.

### FA-20: Literal status code instead of the `status` constant
- Severity: Nit
- Description: One new `HTTPException(404, "order not found")` uses a bare integer while every
  other raise in the file uses `status.HTTP_404_NOT_FOUND`.
- Planting: The deliver endpoint (FA-10) or summary endpoint (FA-11) in
  `app/api/routers/orders.py` raises with `HTTPException(404, ...)`.
- Hidden test: None. Graded by inspection of the diff.

### FA-21: Docstring contradicts the declared status code
- Severity: Nit
- Description: The new endpoint's docstring says "Returns 201" while the decorator has no
  `status_code`, so it returns 200, and the docstring surfaces in OpenAPI.
- Planting: `PATCH /customers/me` or `POST /orders/{id}/deliver` gets a docstring copied from
  `create_customer` that says "Returns 201 with the created record".
- Hidden test: None. Graded by inspection; the OpenAPI description at `/openapi.json` shows the
  wrong sentence.

## Looks wrong but is fine

### FA-CLEAN-01: Looping over `order.items` inside a loop over orders
- Pattern: `recent_total` or a summary endpoint does `sum(len(o.items) for o in rows)` or
  iterates `for item in o.items` for each order in a list.
- Why it is fine: `Order.items` is declared `lazy="selectin"` in `app/db/models.py`, so
  SQLAlchemy loads all items for the batch in one extra `IN` query. Two statements total,
  regardless of row count.
- What a reviewer might wrongly say: "This is an N+1: every order triggers a query for its
  items."

### FA-CLEAN-02: Returning a dict of ORM rows through `Page[OrderOut]`
- Pattern: `list_orders` returns `{"items": rows, "limit": ..., "offset": ...}` where `rows` are
  `Order` instances, with `response_model=Page[OrderOut]`.
- Why it is fine: `OrderOut` has `model_config = ConfigDict(from_attributes=True)` and is the
  explicit allowlist written for this endpoint. Convention 9 forbids returning a row through a
  model not written for the endpoint, not returning rows at all. `customer_id`,
  `idempotency_key`, and `updated_at` are absent from `OrderOut`, so they never leave the
  process.
- What a reviewer might wrongly say: "This returns ORM objects directly and leaks the whole row;
  serialize by hand."

### FA-CLEAN-03: `get_db` commits after a read-only request
- Pattern: `get_db` in `app/api/deps.py` calls `session.commit()` after `yield` even for `GET`
  handlers.
- Why it is fine: Convention 8 makes the request dependency the single transaction owner, so
  every handler gets the same commit/rollback semantics. A commit on a session with no pending
  changes is a no-op at the ORM level and ends the read transaction cleanly, which is what you
  want under `REPEATABLE READ` connections anyway.
- What a reviewer might wrongly say: "Committing on every GET is wasteful and dangerous; only
  commit in write handlers."

### FA-CLEAN-04: Cancel fetches the order twice
- Pattern: `cancel_order` calls `OrderRepository(db).get_for_customer(order_id,
  principal.customer)` and discards the result, then `service.cancel(order_id)` loads the order
  again via `OrderRepository.get`.
- Why it is fine: Both loads run on the same request session inside one transaction, so the
  second is served from the identity map without a new SELECT, and the ownership check is what
  keeps `cancel` safe for customer keys. The service keeps its id-based signature so the worker
  and scripts can call it without a principal.
- What a reviewer might wrongly say: "Redundant query, and a TOCTOU race between the check and
  the cancel; pass the loaded order into the service."
