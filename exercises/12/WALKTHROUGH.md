# Exercise 12 walkthrough: cart preview totals

Mode: teach. Domain: rewrite:domain (`app/domain`). Difficulty: rewrite.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/28
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/38
- Feature: `app/domain/checkout.py` turns the raw cart payload the storefront
  posts (all values are strings) into a `Receipt`: a `Quote` plus the receipt
  text the page prints.

Read the exercise PR with its inline comments first, then the rewrite PR with
its per-hunk commentary, then this file.

This is a rewrite exercise. There are no planted defects. Every number the
feature produces is right, the suite is green, ruff and mypy are green, and
there is nothing here to find in the usual sense. The exercise is to say what
is wrong with the shape, in what order you would change it, and, just as
important, what you would refuse to build.

## How to read this diff

Two files, 205 lines, and 165 of them are one function. The reading order is
not top to bottom.

1. **`app/domain/pricing.py`, which is not in the diff.** Open it first. This
   is the move that decides the whole review. The module already has `Line`
   (which validates quantity and price), `Discount.apply` (percent, fixed,
   threshold, capped at the subtotal), `best_discount` (biggest wins, ties to
   the first listed), `TAX_RATES` with `tax_rate_for` (unknown region means
   zero), and `quote` (subtotal, discount, tax, total). Write that list down.
2. **`app/domain/checkout.py`, the last twenty lines.** Read the return value
   before the body. `Receipt(quote=Quote(...), text=...)`. The function
   assembles a `Quote` by hand out of four local variables, which tells you
   before you read any logic that it is doing the pricing module's job.
3. **The signature and the docstring.** "Parse a raw cart, price it, and
   render the receipt." Three verbs, one function. The docstring is the
   confession.
4. **The body, in one pass, only asking "what job is this line doing".** Mark
   where each job starts: parse and validate items at line 39, sum at 73,
   pick a discount at 81, tax at 128, format at 145, assemble at 156. Six
   jobs, one scope, and every local is alive for the whole thing.
5. **`tests/test_checkout.py`.** Four tests, and they are the contract you are
   not allowed to move. Notice what they pin: exact amounts, the receipt's
   first and last line, the empty-cart error. Notice what they do not pin:
   most of the error messages, the threshold boundary, five of the six tax
   rates. That gap is the risk of a rewrite, and it is the first thing to say
   out loud.

## What to grep for

- `grep -n "Decimal(100)" app/domain/checkout.py`: six hand-rolled percent
  calculations where `Money.percent` exists.
- `grep -n "^TAX_RATES" -A8 app/domain/pricing.py` next to lines 128 to 139 of
  the new file: the same six rates, written twice, in two files, with one
  test between them.
- `grep -c "^\s*#" app/domain/checkout.py`: twenty-four comments in a
  165-line file, and read five of them at random to see whether any says why.
- `grep -n "raise ValueError" app/domain/checkout.py`: fourteen raises. Then
  `grep -n "raise ValueError" app/domain/pricing.py`: the two about quantity
  and price are already there, with identical wording.
- Count leading spaces on the deepest line: 28, and the function body
  starts at 4. That is six levels of nesting inside one function.

## The reasoning chain for each smell

### 1. God function (`compute_order_total`, line 26)

The chain starts at the return value, not the top. The function builds a
`Quote` itself, so it must be computing subtotal, discount, tax and total.
`quote()` in `pricing.py` computes exactly those four from lines and
discounts. So the question is not "should this be split" but "why is this
computing anything at all".

Then check the pieces one at a time, and each one lands on code that already
exists: the `q > 0` and price checks are `Line.__post_init__`, with the same
messages; the percent, fixed and threshold branches are `Discount.apply`,
including the cap at the subtotal; the running best-so-far with the tie rule
is `best_discount`; the tax ladder is `tax_rate_for`. What is genuinely new in
this file is parsing strings into domain types, and formatting a receipt.

That is the rewrite, and it is a subtraction: `parse_items`,
`parse_discounts`, `format_receipt`, and a `compute_order_total` that calls
`quote` in the middle. The file gets shorter, from 165 lines to 115.

The trap here is proposing structure instead of deletion. A `CartParser`
class, a step pipeline, or a `TaxPolicy` protocol all leave the duplicated
pricing in place and add a layer on top of it. The winning answer removes
code.

### 2. Deep nesting (line 39, and again at 81)

Do not argue about nesting on style grounds. Argue from a concrete reading
task: find the rule that produces "quantity must be positive". It is on line
68; the condition it belongs to is on line 57. Eleven lines and three
indentation levels separate a rule from its message, and the only way to pair
them is to count spaces.

The inversion is mechanical: every `if x: ... else: raise` becomes
`if not x: raise`, and the body straightens. The insight beyond the mechanical
part is that once items become `Line` objects, most of the guards are not
inverted, they are deleted, because the domain type raises the same errors in
the same order.

The order matters, which is why the rewrite constructs the `Line` before
checking `MAX_QUANTITY`. Say that out loud in a review; it is the kind of
detail that separates "I moved the code" from "I know what the code did".

### 3. Magic numbers (line 60, lines 128 to 139, lines 149 to 154)

Three different flavors, and they deserve three different answers, which is
the actual test here.

- The tax rates are not a naming problem. They are a duplicated table, and the
  fix is deletion, not `US_CA_RATE = Decimal("7.25")`.
- `999` is a real rule with no name. It becomes `MAX_QUANTITY`, a module
  constant, not a setting: an operator will never want a different cart cap in
  staging, and a config entry for a fixed value adds a startup failure mode
  and a knob nobody turns.
- `:<10` repeated five times is one layout decision written five times.
  `LABEL_WIDTH` says it once.

A candidate who promotes all three to environment variables has made the code
worse and should be asked who would change each value.

### 4. Narrating comments and one-letter names (line 33 onward)

"# loop over the items" above `for it in raw_items:` costs a line and earns
nothing, and it will be wrong the day the loop changes, silently, because no
test reads comments. The tell that these are compensating for names rather
than explaining anything: `t` is tax, `tot` is the total, `tmp` is the
receipt, `x` is a SKU, and `r[1] * r[2]` is a line subtotal.

The fix is a rename commit and a deletion commit, kept separate from every
logic change so a reviewer can skim them. The judgment call is which comments
to keep. Keep the one that says why a `Line` is constructed before the
quantity cap is checked, because a reader cannot recover that from the code.
Delete every comment that restates the line beneath it.

## The clean-code trap

Line 108: `if Money(fv, currency) <= sub:`, with the minimum on the left. It
reads like an inverted comparison and it is easy to flag as a bug. It is
correct: `Money` implements only `__lt__` and `__le__`, so the minimum has to
be the left operand, and the branch matches `Discount.apply` exactly. Asserting
a bug here costs five points as a false positive. Asking whether `Money`
should also define `__ge__` is a fair question and costs nothing.

Note how the rewrite handles it: the comparison is not flipped, it is deleted
along with the rest of the discount arithmetic. Rewriting a line you were
suspicious of is not the same as fixing it.

## Design and tests

A strong reviewer notices each of the four design findings the same way this
walkthrough does: read the return value first, then ask which job each
section of the function is doing, then check whether that job already has a
home elsewhere in the codebase. The god function, the nesting, the duplicated
tax table, and the narrating comments all turn up on that same pass, not on
four separate readings. What makes them findings rather than taste is that
each one names a concrete cost: two copies of the tax table will drift, six
levels of nesting separate a rule from its error message, and a comment that
restates its line will go stale the day the line changes.

The test finding is different because it is not in the production file at
all. A reviewer who reads `tests/test_checkout.py` after the smells, instead
of skipping it as boilerplate, notices that `test_empty_cart_is_rejected`
catches `pytest.raises(Exception)` for a function that raises exactly one
kind of error with exactly one message. The tell is the same one that flags a
broad `pytest.raises` anywhere: the assertion is wider than the thing it is
supposed to pin down, so a future change that broke the contract in a
different way, say a `TypeError` from a typo, would still show green.

Two interviewer questions:

1. If you had found only the god function and none of the other three
   smells, would you still have flagged the empty-cart test? What in the
   test itself, independent of the production code, gives it away?
2. `pytest.raises(ValueError)` with no `match` argument would already be an
   improvement over `pytest.raises(Exception)`. Why does the reference fix
   also assert the message, and when would you stop short of that?

## Questions to ask the author

- The PR note says the region rates match `TAX_RATES`. What stopped you from
  calling `tax_rate_for`? Is there a case where the two tables should differ?
- `tests/test_checkout.py` pins two tax rates and four error messages. If I
  restructured this function today, which of the remaining rules would tell me
  I broke it?
- The storefront posts strings. Is `compute_order_total` the only caller, or is
  there a service or router coming that will want the parsed `Quote` without
  the text?
- The quantity cap is 999. Where did that number come from, and is it a
  business rule, a database limit, or a guess?

## Five questions an interviewer would ask about the rewrite

1. You deleted the explicit `quantity > 0` and negative-price checks and let
   `Line` raise instead. How did you convince yourself the messages and their
   order did not change, and what would you have done if `Line` had raised a
   different exception type?
2. "threshold discount needs min_subtotal" now comes from `Discount.apply`
   during pricing instead of from the parser. Construct the payload where a
   caller could observe that difference. Does it matter, and why did you accept
   it instead of keeping the parser check?
3. You split into three functions plus wiring. Why not five, with a separate
   `parse_item` and `parse_discount` for a single row? What would push you to
   split further?
4. `MAX_QUANTITY` is a module constant and the tax rates were deleted rather
   than named. Both are "magic numbers". Explain the rule you used to treat
   them differently, and where `LABEL_WIDTH` falls under it.
5. Your commits are: reuse the domain, move, flatten, name, rename. Why is the
   move a separate commit from the flatten, given that both touch the same
   lines and the reviewer sees only the final diff?
