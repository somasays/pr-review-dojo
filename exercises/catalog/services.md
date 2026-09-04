# Services defect catalog

Defects for exercises that touch `app/services/` (`order_service.py`, `notification.py`, `retry.py`, `pricing_service.py`, `config.py`) and the code they lean on in `app/domain/dates.py` and `app/db/repositories.py`. `/exercise` and `/seed` pick entries by id, plant them inside an honest-looking feature PR using the Planting notes, and write the hidden test described under Hidden test into `solutions_tests/` on the solution branch. Several entries share one plausible feature (a payment gateway in `mark_paid`, tracking numbers in `order_shipped`, a `send_many` digest, an unpaid-order expiry sweep) so two or three can be combined into a single PR without the diff looking staged. Severity follows the scale in `CLAUDE.md`: production impact at realistic load decides, not how hard the defect is to spot. Every exercise also plants one entry from the "Looks wrong but is fine" section; a reviewer who asserts a defect there earns a false positive.

## Defects

### SV-01: Double cancel restores stock twice
- Severity: Blocker
- Description: The early return for an already cancelled order is moved below the restock loop in `OrderService.cancel`, so a replayed cancel adds every item's quantity back to `Product.stock` a second time and inventory drifts upward until the store oversells.
- Planting: In `app/services/order_service.py`, `OrderService.cancel`. Feature: add a `reason: str | None` parameter that is logged and put in the email body. While reordering the method, the author places the `if order.status == OrderStatus.CANCELLED: return order` guard after the `for item in order.items: item.product.stock += item.quantity` loop, so the guard still prevents the second email but not the second restock. Convention 3 (status changes are no-ops when already in the target state) is broken silently because `_move` still returns early.
- Hidden test: `db`, `seeded`, `OrderService(db, PricingService(), NotificationService(InMemorySender(), Settings()))` as in `tests/test_order_service.py`. Create the standard two-item order, `db.commit()`, call `cancel` twice. Assert `seeded["products"]["GADGET"].stock == 5` and exactly one message in `sender.sent`. The defect gives 6.

### SV-02: Commit in the middle of cancel leaves stock released on a pending order
- Severity: Blocker
- Description: `OrderService.cancel` calls `self.session.commit()` after the restock loop and before `_move` and the notification, so if the status change or the email fails the caller's rollback undoes nothing: stock is back on the shelf while the order is still `pending_payment` and can still be paid and shipped.
- Planting: In `app/services/order_service.py`, `OrderService.cancel`. Feature: "release stock immediately on cancel so the storefront sees it even if the email gateway is down". The author adds `self.session.commit()` right after the restock loop with a comment to that effect. Convention 8 (the caller owns the transaction) is the tell.
- Hidden test: `db`, `seeded`, service built with `InMemorySender(fail_times=3)` and `Settings(notify_retries=3)` so the cancel email exhausts retries. Create the standard order, `db.commit()`, call `cancel` inside `pytest.raises(RetryExhausted)`, then `db.rollback()`. Assert `order.status == "pending_payment"` and `seeded["products"]["GADGET"].stock == 4`. The defect leaves stock at 5 with the order still pending.

### SV-03: Charge failure swallowed, order marked paid anyway
- Severity: Blocker
- Description: `OrderService.mark_paid` wraps the new `gateway.charge` call in `except Exception` that only logs, then proceeds to `_move(order, PAID)` and sends the confirmation, so a declined or unreachable gateway still produces a paid order and a "confirmed" email.
- Planting: In `app/services/order_service.py`, `OrderService.mark_paid`. Feature: introduce a `PaymentGateway` protocol with `charge(amount: Money, idempotency_key: str) -> str` and an `InMemoryGateway`, injected through `OrderService.__init__`. The mistaken code:
  ```python
  try:
      self.gateway.charge(Money(order.total, order.currency), f"order:{order.id}")
  except Exception as exc:
      log.warning("charge for order %s failed: %s", order.id, exc)
  self._move(order, OrderStatus.PAID)
  ```
- Hidden test: `db`, `seeded`, service built with a gateway stub whose `charge` raises a `PaymentDeclined` exception defined in the PR. Create the standard order, `db.commit()`, call `mark_paid` inside `pytest.raises(PaymentDeclined)`. Assert `order.status == "pending_payment"` and `sender.sent == []`. The defect returns normally with status `paid` and one email.

### SV-04: Invalid transition swallowed in cancel, shipped order gets a cancellation email
- Severity: Blocker
- Description: `OrderService.cancel` catches `InvalidTransition` around the state check, logs it, and continues to the notification, so cancelling a shipped or delivered order returns 200, leaves the status untouched, and emails the customer that the order was cancelled and will be refunded.
- Planting: In `app/services/order_service.py`, `OrderService.cancel`. Feature: "cancel should never 500 from the admin console". The author replaces the `transition(...)` call that raises with `try: ... except InvalidTransition as exc: log.info("cancel skipped: %s", exc)` and forgets that the notification below still runs. The router in `app/api/routers/orders.py` still maps `InvalidTransition` to 409, which now never fires.
- Hidden test: `db`, `seeded`, standard service. Create, `db.commit()`, `mark_paid`, `ship`, then `cancel` inside `pytest.raises(InvalidTransition)`. Assert `order.status == "shipped"` and no message in `sender.sent` has a dedupe key starting with `order-cancelled:`. The defect returns the order and appends the cancellation message.

### SV-05: Settings repr logged with SMTP password and admin keys
- Severity: Blocker
- Description: `load_settings` logs the whole `Settings` dataclass at INFO, and the auto-generated repr includes `smtp_password` and `admin_api_keys`, so every process start writes live credentials into the log pipeline where anyone with log access can use an admin key.
- Planting: In `app/services/config.py`, `load_settings`. Feature: "log effective settings at startup for support". The author adds `log = logging.getLogger(__name__)` and `log.info("settings loaded: %r", settings)` before returning. It reads as a helpful diagnostic because the dataclass has a tidy repr.
- Hidden test: `monkeypatch.setenv("SMTP_PASSWORD", "hunter2")` and `ADMIN_API_KEYS="k-admin-1"`, `caplog.at_level(logging.INFO, logger="app.services.config")`, call `load_settings()`. Assert `"hunter2" not in caplog.text` and `"k-admin-1" not in caplog.text`. The fix either drops the log line or gives `Settings` a `__repr__` that redacts both fields (the test passes either way).

### SV-06: Default admin key when ADMIN_API_KEYS is unset
- Severity: Blocker
- Description: `load_settings` passes `["dev-admin"]` as the default for `_list("ADMIN_API_KEYS", ...)`, so a production deploy that forgets the variable accepts `X-API-Key: dev-admin` on every admin endpoint.
- Planting: In `app/services/config.py`, `load_settings`. Feature: "make local development work without exporting ADMIN_API_KEYS". The author changes the default to `["dev-admin"]` and updates the README table. There is no `is_prod` guard, unlike the existing sqlite check two lines above.
- Hidden test: `monkeypatch.delenv("ADMIN_API_KEYS", raising=False)`, `monkeypatch.setenv("APP_ENV", "prod")`, `monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")` so the sqlite check does not fire first. Assert `load_settings().admin_api_keys == []`. The defect returns `["dev-admin"]`. Also assert a dev-environment call with the variable unset does not contain `"dev-admin"`, so a fix that only guards prod still fails.

### SV-07: Retry around a charge with no idempotency key double-bills on timeout
- Severity: Major
- Description: `mark_paid` wraps `gateway.charge` in `retry()` with `TimeoutError` retryable but sends no idempotency key, so a timeout on the response after the charge went through bills the card twice. Convention 10 allows retry only for idempotent operations.
- Planting: In `app/services/order_service.py`, `OrderService.mark_paid`, same gateway feature as SV-03. The author writes `retry(lambda: self.gateway.charge(amount), RetryPolicy(attempts=3, retry_on=(TimeoutError, ConnectionError)), sleep=lambda _s: None)` and the `charge` signature has `idempotency_key: str | None = None`. The fix passes `f"order:{order.id}"` (or drops the retry).
- Hidden test: `db`, `seeded`, service built with a gateway stub that records every `charge` call in a list keyed by `idempotency_key`, dedupes when the key was seen before, and raises `TimeoutError` once immediately after recording the first charge. Create, `db.commit()`, `mark_paid`. Assert exactly one recorded charge. The defect records two because no key is sent.

### SV-08: Dedupe key contains a timestamp, so replays are never deduplicated
- Severity: Major
- Description: `NotificationService.order_shipped` builds `dedupe_key=f"order-shipped:{order_id}:{utcnow().isoformat()}"`, so every call, including a webhook replay or a worker retry after a timeout, gets a fresh key and the gateway sends the email again. Convention 3 relies on a stable `dedupe_key`.
- Planting: In `app/services/notification.py`, `NotificationService.order_shipped`. Feature: add `tracking_number: str` to the shipped email. The author includes both the tracking number and a `utcnow()` stamp in the key "so a corrected tracking number can be resent". A tracking number alone in the key is acceptable; the timestamp is the defect.
- Hidden test: `NotificationService(InMemorySender(), Settings(notify_retries=1))`, call `order_shipped("a@example.com", 8, tracking_number="T1")` twice. Assert `sender.sent[0].dedupe_key == sender.sent[1].dedupe_key` and that the key starts with `order-shipped:8`. The defect produces two different keys.

### SV-09: Whole batch under one retry re-sends the messages that already went out
- Severity: Major
- Description: `NotificationService.send_many` passes a lambda that loops over all messages into `retry()`, so a `ConnectionError` on the third message re-runs the loop from the first and the first two are delivered again; retries belong around each single send.
- Planting: In `app/services/notification.py`, new public `send_many(messages: list[Message]) -> None` for a "daily digest" feature. The mistaken code is `retry(lambda: [self.sender.send(m) for m in messages], self.policy, sleep=lambda _s: None)`. The fix is `for m in messages: self._deliver(m)`.
- Hidden test: A `Sender` stub in the test that raises `ConnectionError` on exactly its third `send` call and records every successful message. `NotificationService(stub, Settings(notify_retries=3))`, call `send_many` with four messages with distinct dedupe keys. Assert the list of recorded dedupe keys equals the four inputs in order with no duplicates. The defect records six.

### SV-10: Retry policy catches Exception, so permanent errors are retried three times
- Severity: Major
- Description: The notification `RetryPolicy` is built with `retry_on=(Exception,)`, so a `ValueError` for a malformed recipient or a bug in the sender is retried to exhaustion and surfaces as `RetryExhausted`, tripling gateway load on bad input and hiding the real error type from the router.
- Planting: In `app/services/notification.py`, `NotificationService.__init__`. Feature: "the gateway client raises its own exception classes, retry all of them". The author adds `retry_on=(Exception,)` to the `RetryPolicy(...)` call instead of listing the gateway's transient classes.
- Hidden test: A sender stub whose `send` raises `ValueError("invalid recipient")` and counts calls. `NotificationService(stub, Settings(notify_retries=3))`, call `order_confirmed` inside `pytest.raises(ValueError)`. Assert the stub was called once. The defect raises `RetryExhausted` after three calls.

### SV-11: Day window computed from the local date of a non-UTC timestamp
- Severity: Major
- Description: `OrderService.created_on_day(now)` takes `now.date()` instead of `ensure_utc(now).date()`, so for a `now` carrying a non-UTC offset the window is the wrong calendar day: orders created after 00:00 UTC are missed until the local day rolls over.
- Planting: In `app/services/order_service.py`, new public `created_on_day(now: datetime | None = None) -> Sequence[Order]` for a "today's orders" admin report that calls `OrderRepository.created_between`. The author writes `day = (now or utcnow()).date()` and builds `start = datetime.combine(day, time.min, tzinfo=UTC)`. Convention 7 says timestamps are normalized through `ensure_utc`.
- Hidden test: `db`, `seeded`, standard service. Create an order, set `order.created_at = datetime(2026, 8, 2, 6, 0, tzinfo=UTC)` before `db.commit()`. Call `created_on_day(datetime(2026, 8, 1, 23, 30, tzinfo=ZoneInfo("America/Los_Angeles")))`, which is 06:30 UTC on August 2. Assert the order id is in the result. The defect queries August 1 and returns nothing. Do not assert on `created_at.tzinfo`; SQLite drops it.

### SV-12: Order discount split per line by plain division drops cents
- Severity: Major
- Description: `OrderService.receipt_lines` computes each line's share of the discount as `Money(order.discount / len(order.items))`, which quantizes every share down, so the shares no longer sum to `order.discount` and receipts disagree with the order total by a cent per line. `Money.allocate` exists for exactly this.
- Planting: In `app/services/order_service.py`, new public `receipt_lines(order_id: int) -> list[tuple[str, Money]]` used to build the confirmation email body in `mark_paid`. The mistaken line is `share = Money(order.discount / len(order.items), order.currency)` inside the loop. The fix is `shares = Money(order.discount, order.currency).allocate(len(order.items))`.
- Hidden test: `db`, `seeded`, standard service. Add a third product in the test so the order has three lines and create it with `["welcome10"]` so `order.discount` is not divisible by three (for example WIDGET 2, GADGET 1, and a `Product(sku="THING", unit_price=Decimal("1.03"), stock=10)`). Call `receipt_lines`, `sum_money` the second elements, and assert equality with `Money(order.discount, order.currency)`. The defect is one cent short.

### SV-13: Confirmation sent before flush, dedupe key is order-confirmed:None
- Severity: Major
- Description: In `OrderService.create` the free-order confirmation is sent before `self.orders.add(...)` flushes, so `order.id` is `None`, every free order shares the dedupe key `order-confirmed:None`, and the gateway drops all but the first as duplicates while the subject reads "Order None confirmed".
- Planting: In `app/services/order_service.py`, `OrderService.create`. Feature: "orders with a zero total skip payment and are confirmed immediately". The author adds `if q.total.is_zero(): self.notifications.order_confirmed(customer.email, order.id, str(q.total.amount))` right after constructing the `Order`, above the `begin_nested()` block. The existing tests never create a free order, so nothing catches it.
- Hidden test: `db`, `seeded`, standard service. Add `Product(sku="FREEBIE", unit_price=Decimal("0"), stock=10)` in the test, `db.commit()`, create an order for one FREEBIE, `db.commit()`. Assert `sender.sent[0].dedupe_key == f"order-confirmed:{order.id}"` and `order.id is not None`. The defect yields `order-confirmed:None`.

### SV-14: Retry settings re-read from the environment on every send, injected Settings ignored
- Severity: Minor
- Description: `NotificationService._deliver` builds its `RetryPolicy` from `load_settings()` on every call instead of the `Settings` passed to `__init__`, so the environment is parsed per email, a bad `NOTIFY_RETRIES` raises `ConfigError` at send time instead of startup, and the injected settings that tests and the worker rely on have no effect.
- Planting: In `app/services/notification.py`, `NotificationService._deliver`. Feature: "let ops tune retries without a restart". The author replaces `self.policy` with `RetryPolicy(attempts=load_settings().notify_retries, backoff_seconds=load_settings().notify_backoff_seconds)` inline. README says settings are read once from the environment.
- Hidden test: `NotificationService(InMemorySender(fail_times=4), Settings(notify_retries=5))`, call `order_shipped("a@example.com", 8)`. The autouse `_settings_env` fixture sets `NOTIFY_RETRIES=3`, so the defect raises `RetryExhausted` after three attempts while the fix sends on attempt five. Assert one message in `sender.sent`.

### SV-15: Broad except re-raised as NotificationError without the cause
- Severity: Minor
- Description: `_deliver` catches `Exception` and raises a new `NotificationError(f"could not send {subject}")` without `from exc`, so `__cause__` is `None`, the traceback of the original failure is gone, and callers can no longer tell a `RetryExhausted` on a flaky gateway from a `TypeError` in the sender.
- Planting: In `app/services/notification.py`, `NotificationService._deliver`. Feature: "one exception type for the API to map to 502". The author wraps the `retry(...)` call in `try: ... except Exception: raise NotificationError(...)`. The fix narrows to `RetryExhausted` and adds `from exc`.
- Hidden test: `NotificationService(InMemorySender(fail_times=5), Settings(notify_retries=2))`, call `order_confirmed` inside `pytest.raises(NotificationError) as info`. Assert `isinstance(info.value.__cause__, RetryExhausted)`. Then a sender stub raising `TypeError` and assert `pytest.raises(TypeError)` propagates unchanged. The defect fails both: cause is `None` and the `TypeError` becomes `NotificationError`.

### SV-16: Service commits after create, taking the transaction away from the caller
- Severity: Minor
- Description: `OrderService.create` calls `self.session.commit()` after `orders.add`, so the request dependency and the worker can no longer roll back the order together with later work in the same unit, and a handler that creates several orders in one session commits them one by one. Convention 8 gives the caller the transaction boundary.
- Planting: In `app/services/order_service.py`, `OrderService.create`, after the `begin_nested()` block. Feature: "return the order id to the worker as soon as it exists". The author adds `self.session.commit()` with a comment "make the id durable before returning".
- Hidden test: `db`, `seeded`, standard service. Call `create`, then `db.rollback()`, then `OrderRepository(db).by_idempotency_key(customer.id, "key-00000001")`. Assert it returns `None` and `seeded["products"]["GADGET"].stock == 5`. The defect leaves the committed order in place with stock at 4.

### SV-17: Weekly digest range built from the local date instead of the UTC default
- Severity: Minor
- Description: `OrderService.weekly_digest_range` calls `DateRange.last_n_days(7, today=date.today())`, overriding the UTC default in `app/domain/dates.py` with the host's local date, so on any server not running in UTC the digest covers the wrong week for part of every day.
- Planting: In `app/services/order_service.py`, new public `weekly_digest_range() -> DateRange` for a digest feature. The author passes `today=date.today()` explicitly "to be clear about which day we mean", importing `date` at module level.
- Hidden test: `monkeypatch.setattr("app.domain.dates.utcnow", lambda: datetime(2026, 8, 2, 1, 0, tzinfo=UTC))` and, with `raising=False`, replace `app.services.order_service.date` with a stub whose `today()` returns `date(2026, 1, 1)`. Assert `weekly_digest_range() == DateRange(date(2026, 7, 26), date(2026, 8, 1))`. The defect ends the range on 2025-12-31.

### SV-18: Ship re-validates products one query per item
- Severity: Minor
- Description: `OrderService.ship` loops over `order.items` and calls `self.products.get(item.product_id)` for each, issuing one SELECT per line instead of a single `by_skus` lookup, so shipping cost grows with order size and the admin bulk-ship screen gets noticeably slower on large orders.
- Planting: In `app/services/order_service.py`, `OrderService.ship`. Feature: "refuse to ship if any product was deleted since the order was placed". The author adds `for item in order.items: self.products.get(item.product_id)` before `_move`. `ProductRepository.by_skus` already returns everything in one query.
- Hidden test: `session_factory`, `seeded`, standard service. Register a `before_cursor_execute` listener on `db.get_bind()` that counts statements. Create an order with two items and one with three (the test adds a third product), commit, reset the counter before each `ship`, and assert both ships issue the same number of statements. The defect issues one more for the larger order.

### SV-19: Money formatted through float in the confirmation email
- Severity: Nit
- Description: `mark_paid` passes `f"{float(order.total):.2f}"` to `order_confirmed` instead of `str(order.total)` or `str(Money(order.total, order.currency))`, which produces the same digits today but reintroduces float into a money path the README forbids.
- Planting: In `app/services/order_service.py`, `OrderService.mark_paid`. Feature: "always show two decimals in the email". The author reaches for float formatting rather than the already quantized `Decimal`.
- Hidden test: Read `app/services/order_service.py` as text with `Path(...).read_text()` and assert `"float("` does not appear. Also assert the confirmation body for the standard order contains `"154.42"` so the fix does not regress the display.

### SV-20: Retry logs "retrying in N seconds" on the final attempt
- Severity: Nit
- Description: The new log line in `retry()` runs after every failure, including the last one, so the log promises a retry that never happens and misleads whoever reads it during an incident.
- Planting: In `app/services/retry.py`, `retry`. Feature: "log the backoff delay". The author adds `log.warning("retrying in %.1fs", policy.delay(attempt + 1))` inside the `except` block without an `if attempt < policy.attempts` guard.
- Hidden test: `caplog.at_level(logging.WARNING, logger="app.services.retry")`, call `retry` with a function that always raises `ConnectionError`, `RetryPolicy(attempts=2)`, `sleep=lambda _s: None`, inside `pytest.raises(RetryExhausted)`. Assert exactly one record contains `"retrying in"`. The defect logs two.

### SV-21: Unused import left behind by the gateway feature
- Severity: Nit
- Description: `order_service.py` imports `timedelta` (or `Decimal`) for an intermediate version of the feature and never uses it; ruff `F401` fails and CI goes red for a reason unrelated to the feature.
- Planting: In `app/services/order_service.py` module imports. Any feature in this file that was reworked mid-PR. Leave `from datetime import timedelta` in place with no usage.
- Hidden test: `subprocess.run(["uv", "run", "ruff", "check", "--select", "F401", "app/services/order_service.py"])` from the repository root and assert `returncode == 0`.

## Looks wrong but is fine

### SV-CLEAN-01: retry() around sender.send
- Pattern: `NotificationService._deliver` calls `retry(lambda: self.sender.send(message), self.policy, ...)`, a retry wrapped around a write to an external system.
- Why it is fine: Convention 10 allows retries for sends, and every `Message` carries a `dedupe_key` (`order-confirmed:{id}` and friends) that the gateway uses to drop a repeat after a partial success. The module docstring in `app/services/notification.py` states this contract. The retry is the whole point of the service.
- What a reviewer might wrongly say: "Retrying a send can email the customer twice, this needs an idempotency check."

### SV-CLEAN-02: stock decremented in memory before the savepoint
- Pattern: In `OrderService.create` the loop `products[i.sku].stock -= i.quantity` runs before `with self.session.begin_nested(): self.orders.add(order, items)`, and the `IntegrityError` branch calls `self.session.rollback()` and returns the winning order.
- Why it is fine: The decrement is an uncommitted change on the same `Session`. If the insert loses the idempotency race the rollback discards it and the winner already decremented stock in its own transaction. If anything else fails, the caller (`get_db` or the worker) rolls back the whole unit under convention 8. There is no window in which the decrement is durable without the order.
- What a reviewer might wrongly say: "Stock is reduced even when the insert fails, so a failed create leaks inventory."

### SV-CLEAN-03: _move flushes and never commits
- Pattern: `OrderService._move` sets `order.status` and calls `self.session.flush()`; no method in the service calls `commit()`.
- Why it is fine: Repositories and services never commit (convention 8). The flush makes the status change visible to later queries in the same transaction and surfaces constraint errors early; `get_db` in `app/api/deps.py` commits when the request succeeds and rolls back otherwise. Adding a commit here is defect SV-16.
- What a reviewer might wrongly say: "Status changes are never persisted, this needs a `session.commit()`."

### SV-CLEAN-04: was_pending snapshot before _move in mark_paid
- Pattern: `mark_paid` reads `was_pending = order.status == OrderStatus.PENDING_PAYMENT` before calling `_move`, then sends the confirmation only `if was_pending`.
- Why it is fine: `_move` returns early when the order is already `paid`, so the snapshot is what makes a replayed `/pay` call a no-op for the email as well as the status, which is convention 3. The read and the write happen on the same row in the same transaction, so there is no race to close, and `test_lifecycle_notifications` pins the behavior.
- What a reviewer might wrongly say: "Checking the status before moving is a time-of-check to time-of-use race; use the return value of `_move` instead."
