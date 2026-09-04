# Logic defect catalog

Pure-logic defects for `app/domain/` (`money.py`, `pricing.py`, `dates.py`, `order_state.py`) and the thin adapter in `app/services/pricing_service.py`. Each entry names the file and function to touch, a feature that would plausibly touch it, the mistaken code, and the hidden test that separates the defect from the fix. `/exercise` and `/seed` pick entries from `## Defects` by severity mix and always plant one entry from `## Looks wrong but is fine` as the false-positive trap. Every planting must pass `uv run mypy` (strict on `app/domain`) and `uv run ruff check .` (rules E, F, I, UP only, so mutable defaults and float comparisons are not caught by lint). Hidden tests live under `solutions_tests/` on the `solutions/N` branch and must fail on the exercise branch and pass on the fix.

## Defects

### LG-01: Float round trip truncates cents in payment export
- Severity: Blocker
- Description: Converting a `Money` to integer cents through `float` truncates toward zero, so amounts like 0.29 become 28 cents and the customer is undercharged.
- Planting: Feature "export order totals to the payment provider in minor units" adds `to_cents(self) -> int` to `Money` in `app/domain/money.py`, written as `return int(float(self.amount) * 100)`. It looks like the obvious conversion and type-checks; the fix is `int(self.amount * 100)`, which is exact because `amount` is already quantized to `CENTS`.
- Hidden test: `Money.of("0.29").to_cents() == 29` and `Money.of("4.35").to_cents() == 435`. The defect returns 28 and 434.

### LG-02: Cancel shortcut bypasses the transition table
- Severity: Blocker
- Description: `can_transition` gains a fallback that lets any non-terminal status move to `CANCELLED`, so paid and shipped orders can be cancelled without a refund and stock is restocked for goods already in transit.
- Planting: Feature "support cancelling held orders" adds `OrderStatus.ON_HOLD` in `app/domain/order_state.py` and, instead of adding it to `TRANSITIONS`, rewrites `can_transition` as `return target in TRANSITIONS[current] or (target is OrderStatus.CANCELLED and current not in TERMINAL)`. `OrderService.cancel` relies on `is_cancellable`, which calls this function.
- Hidden test: `can_transition(OrderStatus.PAID, OrderStatus.CANCELLED)` is `False` and `transition(OrderStatus.SHIPPED, OrderStatus.CANCELLED)` raises `InvalidTransition`. The defect returns `True` and returns `CANCELLED`.

### LG-03: Stacked discounts can exceed the subtotal
- Severity: Blocker
- Description: When a fixed and a percent code are allowed to stack, each is capped against the full subtotal separately, so their sum can be larger than the subtotal and the quote total goes negative.
- Planting: Feature "allow one FIXED code to stack on top of a PERCENT code" changes `quote` in `app/domain/pricing.py` to `discount = sum_money([d.apply(subtotal) for d in stackable], currency)` and then `taxable = subtotal - discount`. `Discount.apply` caps each amount at `subtotal`, which reads as safe, but the cap is per code.
- Hidden test: `quote([Line("PEN", Money.of("4.00"), 1)], [FLAT5, WELCOME10], "US-OR")` has `total == Money.zero()` and `discount == Money.of("4.00")`. The defect gives `discount` of 4.40 and `total` of -0.40.

### LG-04: Item iterator consumed by the missing-sku check
- Severity: Blocker
- Description: `build_lines` accepts an `Iterable` and iterates it twice, so a generator is exhausted by the unknown-sku scan and the line loop builds nothing; every order sent through that path fails with "cannot quote an empty order".
- Planting: Feature "stream cart items from the request without building an intermediate list" changes `PricingService.build_lines` in `app/services/pricing_service.py` to take `items: Iterable[ItemRequest]` and `OrderService.create` passes `(ItemRequest(i.sku, i.quantity) for i in cmd.items)`. The body is unchanged: `missing = [i.sku for i in items if i.sku not in products]` followed by `for item in items:`.
- Hidden test: Call `build_lines(iter([ItemRequest("WIDGET", 2)]), {"WIDGET": product})` and assert one `Line` with quantity 2 is returned. The defect returns an empty list.

### LG-05: Weighted allocation hands out one remainder cent too many
- Severity: Blocker
- Description: The remainder loop uses `i <= rem` instead of `i < rem`, so the allocated parts sum to one cent more than the original whenever there is a remainder, and receipts no longer match the charged total.
- Planting: Feature "spread an order-level discount across lines by quantity" adds `allocate_by(self, weights: list[int]) -> list[Money]` to `Money` in `app/domain/money.py`, modeled on `allocate`. After computing each bucket's floor share, the remainder is distributed with `c = base_i + (1 if i <= rem else 0)`.
- Hidden test: `parts = Money.of("1.00").allocate_by([1, 1, 1])`; assert `sum_money(parts) == Money.of("1.00")` and `parts == [0.34, 0.33, 0.33]`. The defect yields 0.34, 0.34, 0.33, summing to 1.01.

### LG-06: Receipt code lookup indexes an empty tuple
- Severity: Blocker
- Description: A new `Quote.primary_code` property returns `self.applied_codes[0]`, which raises `IndexError` for every order without a discount, and that is most orders.
- Planting: Feature "show the discount code on the receipt" adds `primary_code` to `Quote` in `app/domain/pricing.py` with `return self.applied_codes[0]` typed `-> str`, and the orders router renders it. The author only tested with a code applied.
- Hidden test: `quote([WIDGET], [], "US-NY").primary_code is None` (or `== ""` if the fix picks that contract, the answer key accepts either documented choice). The defect raises `IndexError`.

### LG-07: Trailing window includes one extra day
- Severity: Major
- Description: An `include_today` flag on `last_n_days` moves the end to today but subtracts `n` instead of `n - 1` for the start, producing an n+1 day range that double-counts a day in reports and rewrites one extra partition.
- Planting: Feature "same-day reporting" adds `include_today: bool = False` to `DateRange.last_n_days` in `app/domain/dates.py`: `end = today if include_today else today - timedelta(days=1)` then `return cls(end - timedelta(days=n), end)`. The original `n - 1` is lost in the rewrite.
- Hidden test: `DateRange.last_n_days(7, today=date(2026, 8, 10), include_today=True)` has `days == 7` and `start == date(2026, 8, 4)`. The defect gives 8 days starting on August 3.

### LG-08: Applied codes accumulate across quotes through a mutable default
- Severity: Major
- Description: `quote` gets an `applied: list[str] = []` parameter that it appends to, so the shared default list carries codes from earlier calls and later quotes report discounts they never applied.
- Planting: Feature "report every code considered on the quote" adds `applied: list[str] = []` to `quote` in `app/domain/pricing.py`, does `applied.append(chosen.code)` when a discount is chosen, and returns `applied_codes=tuple(applied)`. `PricingService.quote` never passes the argument. Ruff does not select B006, so lint stays green.
- Hidden test: Call `quote([WIDGET], [WELCOME10], "US-OR")` then `quote([WIDGET], [], "US-OR")`; assert the second result has `applied_codes == ()`. The defect returns `("WELCOME10",)`.

### LG-09: Threshold discount with no minimum never applies
- Severity: Major
- Description: Making `min_subtotal` optional for threshold codes is implemented as `if self.min_subtotal is None or subtotal < self.min_subtotal: return zero`, so the "no minimum" case is treated as "never qualifies".
- Planting: Feature "threshold codes without a minimum" removes the `raise ValueError` in the threshold branch of `Discount.apply` in `app/domain/pricing.py` and replaces it with the combined guard above. mypy is happy because the `None` check narrows the type before the comparison.
- Hidden test: `Discount("ANY15", DiscountKind.THRESHOLD, Decimal("15")).apply(Money.of("100.00")) == Money.of("15.00")`. The defect returns zero.

### LG-10: Per-line tax rounds half to even
- Severity: Major
- Description: A line-level tax helper quantizes with `Decimal.quantize(CENTS)` and no rounding argument, which uses the context default `ROUND_HALF_EVEN`, so a line's tax can differ by a cent from `Money.percent` and the receipt lines no longer sum to the order tax.
- Planting: Feature "itemized tax on receipts" adds `line_tax(line: Line, rate: Decimal) -> Money` to `app/domain/pricing.py` written as `Money((line.subtotal.amount * rate / 100).quantize(CENTS), line.subtotal.currency)`. The value is already rounded before `Money.__post_init__` gets a chance to apply `ROUND_HALF_UP`.
- Hidden test: `line_tax(Line("A", Money.of("53.30"), 1), Decimal("5")) == Money.of("2.67")` and equals `Money.of("53.30").percent(Decimal("5"))`. The defect returns 2.66.

### LG-11: Timezone-aware timestamp bucketed by local date
- Severity: Major
- Description: A partition-key helper calls `.date()` on an aware datetime without `ensure_utc`, so events from non-UTC clients land in the wrong `dt` partition and the daily aggregate counts them on the wrong day.
- Planting: Feature "derive the partition key from `placed_at`" adds `partition_for(ts: datetime) -> str` to `app/domain/dates.py` as `return to_dt(ts.date())`. It type-checks, and every timestamp in the test fixtures is already UTC, so nothing fails locally.
- Hidden test: `partition_for(datetime(2026, 8, 2, 1, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))) == "2026-08-01"`. The defect returns "2026-08-02". A second assertion checks that a naive datetime raises `ValueError`.

### LG-12: Threshold boundary turned exclusive
- Severity: Major
- Description: While adding a cap to threshold discounts the qualifying test becomes `subtotal <= self.min_subtotal` returns zero, so a cart that lands exactly on the advertised minimum no longer gets the discount.
- Planting: Feature "cap threshold discounts at a maximum amount" rewrites the threshold branch of `Discount.apply` in `app/domain/pricing.py` and flips the guard to `if subtotal <= self.min_subtotal:` while keeping the docstring "only when subtotal >= min_subtotal".
- Hidden test: `BULK15.apply(Money.of("200.00")) == Money.of("30.00")`. The defect returns zero. A neighbor assertion checks 199.99 still returns zero so the fix cannot overshoot.

### LG-13: Free orders divide by a zero subtotal
- Severity: Major
- Description: Computing each line's share of the order divides by `subtotal.amount`, which raises `decimal.DivisionByZero` for promotional orders where every unit price is 0.00, even though `quote` documents that free orders must produce a valid quote.
- Planting: Feature "prorate the discount across lines" adds `line_shares(lines: list[Line], discount: Money) -> list[Money]` to `app/domain/pricing.py` using `share = discount * (ln.subtotal.amount / subtotal.amount)` per line. `Line` allows a zero unit price, so a free sample order crashes at checkout.
- Hidden test: `line_shares([Line("SAMPLE", Money.zero(), 1)], Money.zero()) == [Money.zero()]`. The defect raises `decimal.DivisionByZero`.

### LG-14: Discount value compared against a float threshold
- Severity: Minor
- Description: A free-shipping check compares `subtotal.amount` (Decimal) with the float constant `49.99`; the float is slightly above 49.99, so a cart of exactly 49.99 misses free shipping, and it breaks the Decimal-for-money convention.
- Planting: Feature "free shipping over 49.99" adds `FREE_SHIPPING_MIN = 49.99` and `qualifies_for_free_shipping(subtotal: Money) -> bool: return subtotal.amount >= FREE_SHIPPING_MIN` in `app/domain/pricing.py`. mypy accepts Decimal-to-float comparison, so only a reviewer catches it.
- Hidden test: `qualifies_for_free_shipping(Money.of("49.99")) is True`. The defect returns `False` because `Decimal("49.99") >= 49.99` is `False`.

### LG-15: Negative discount value passes validation
- Severity: Minor
- Description: New `Discount.__post_init__` validation rejects percent values over 100 but not negative values, so an admin-entered `-5` fixed code becomes a surcharge that `apply` returns as a negative "discount".
- Planting: Feature "admin-managed discount codes" adds `__post_init__` to `Discount` in `app/domain/pricing.py` with only `if self.kind is DiscountKind.PERCENT and self.value > 100: raise ValueError(...)`. The `apply` docstring still promises a non-negative result.
- Hidden test: `Discount("BAD", DiscountKind.FIXED, Decimal("-5"))` raises `ValueError`. The defect constructs it and `apply(Money.of("10.00"))` returns -5.00.

### LG-16: Chunk splitter produces chunks one day too long
- Severity: Minor
- Description: A rewrite of `DateRange.split` computes `chunk_end = cur + timedelta(days=chunk_days)`, dropping the `- 1`, so `split(7)` on a 14-day range yields 8 and 6 day chunks instead of two weeks; backfill batches are uneven and the last chunk is silently smaller.
- Planting: Feature "backfill in weekly batches" touches `split` in `app/domain/dates.py` to add a `max_chunks` guard and loses the `- 1` in the same edit.
- Hidden test: `[r.days for r in DateRange(date(2026, 8, 1), date(2026, 8, 14)).split(7)] == [7, 7]`. The defect gives `[8, 6]`.

### LG-17: Reversed sort picks the last tied discount
- Severity: Minor
- Description: `best_discount` is rewritten as `sorted(discounts, key=...)[::-1][0]`; slicing after an ascending sort reverses the order of tied keys, so ties go to the last code listed and the receipt shows a different code than the documented "first one listed".
- Planting: Feature "expose the ranked discount list" rewrites `best_discount` in `app/domain/pricing.py` to build `ranked = sorted(discounts, key=lambda d: d.apply(subtotal).amount)[::-1]` and return `ranked[0]` when its amount is positive.
- Hidden test: Two percent codes `A` and `B` both at 10 on a 100.00 subtotal; `best_discount(subtotal, [A, B]).code == "A"`. The defect returns `B`.

### LG-18: Default today comes from the local clock
- Severity: Minor
- Description: `last_n_days` defaults `today` to `datetime.now().date()` instead of `utcnow().date()`, so on a host not set to UTC the batch job computes the wrong window for an hour or more each day.
- Planting: Feature "same-day reporting" (or any edit to `last_n_days` in `app/domain/dates.py`) replaces `today = today or utcnow().date()` with `today = today or datetime.now().date()`. `datetime` is already imported, so nothing looks out of place.
- Hidden test: Monkeypatch `app.domain.dates.utcnow` to return `datetime(2026, 8, 10, 23, 30, tzinfo=UTC)` and `datetime.now` to a naive `datetime(2026, 8, 11, 1, 30)`; assert `DateRange.last_n_days(1).end == date(2026, 8, 9)`. The defect returns August 10.

### LG-19: Docstring promises half-up rounding the code does not do
- Severity: Nit
- Description: A new `Money.round_down()` helper's docstring says "rounded half up to cents" (copied from `percent`) while the body uses `ROUND_DOWN`, so the next reader trusts the wrong one.
- Planting: Feature "floor amounts for gift-card redemption" adds `round_down` to `Money` in `app/domain/money.py` with the pasted docstring. Behavior is correct; only the docstring lies.
- Hidden test: None needed; the fix grade checks the docstring text. A reviewer gets credit for pointing at the mismatch.

### LG-20: Unused rounding import left behind
- Severity: Nit
- Description: The PR imports `ROUND_HALF_EVEN` alongside `ROUND_HALF_UP` in `app/domain/money.py` and never uses it, hinting at an abandoned experiment.
- Planting: Any feature touching `money.py` adds the import; ruff `F401` would catch it, so plant it in a file where the name is referenced only inside a comment or a string, such as a `# TODO: switch to ROUND_HALF_EVEN` note plus the import in `app/domain/pricing.py` where `F401` is satisfied by an `__all__` entry.
- Hidden test: None; ruff and the fix grade cover it.

## Looks wrong but is fine

### LG-CLEAN-01: Reverse sort keeps tie order
- Pattern: `best_discount` implemented as `sorted(discounts, key=lambda d: d.apply(subtotal), reverse=True)[0]` (or `max(discounts, key=...)`), with the docstring "ties go to the first one listed".
- Why it is fine: Python's sort is stable and `reverse=True` preserves the original order among equal keys, so the first tied discount is still first; `max` also returns the first maximal element. `Money` defines `__lt__`, which is all `sorted` needs.
- What a reviewer might wrongly say: "reverse=True flips the tie order, so equal discounts now pick the last code listed."

### LG-CLEAN-02: `int(self.amount * 100)` in `allocate`
- Pattern: `cents = int(self.amount * 100)` on a `Money` amount, with no explicit rounding.
- Why it is fine: `Money.__post_init__` quantizes every amount to `CENTS`, so `amount * 100` is an integer-valued `Decimal` and `int()` is exact. Only a `float` round trip (see LG-01) would lose cents.
- What a reviewer might wrongly say: "int() truncates, this drops fractional cents."

### LG-CLEAN-03: `today = today or utcnow().date()`
- Pattern: An optional `date` parameter defaulted with `or` rather than `if today is None`.
- Why it is fine: `date` objects are always truthy (and `datetime` at midnight has been truthy since Python 3.5), so the `or` form is equivalent to an explicit `None` check here. The pattern already exists in `DateRange.last_n_days`.
- What a reviewer might wrongly say: "a falsy date will be silently replaced with today."

### LG-CLEAN-04: Strict `<` when capping a discount at the subtotal
- Pattern: In `Discount.apply`, `if subtotal < off: return subtotal` followed by `return off`.
- Why it is fine: When `off == subtotal` the two branches return equal `Money` values, so `<` and `<=` are indistinguishable; the boundary is exact on cents because both sides are quantized `Decimal`s.
- What a reviewer might wrongly say: "this should be `<=`, at equality the discount exceeds the cap."
