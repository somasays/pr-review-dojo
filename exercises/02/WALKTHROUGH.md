# Exercise 02 walkthrough: tiered volume discounts

Teach mode, domain `logic`, difficulty easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/4
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/10

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## How to read this diff

The PR adds one feature to the purest part of the codebase, so the whole
review fits in two files. Money bugs hide in three places: boundaries,
rounding, and the order of operations. Read for those, in this order.

1. **Read the PR description and write down the contract.** It promises three
   things: 10 units gets 5 percent, 50 units gets 12 percent, and the tier is
   taken on top of the best code. Those three sentences are the test oracle
   for everything below. A number in a PR description is a number you can
   check against the code in under a minute.
2. **Open `app/domain/money.py` first, not `pricing.py`.** It is the smaller
   change and everything in pricing is built on it. One new method,
   `percent_down`. Check what it does, then check that the docstring says the
   same thing.
3. **Open `app/domain/pricing.py` top to bottom, but read `quote` last.**
   Imports, then the new type and its validation, then the lookup, then the
   amount helper, then `best_discount`, then `quote`. Each of the first four
   is small enough to hold in your head, and `quote` is where they combine, so
   you want the pieces settled before you get there.
4. **Grep for the callers.** `grep -rn "quote(" app/` shows
   `PricingService.quote` and `OrderService.create`. That tells you what a
   wrong `Quote` does next: `order.discount`, `order.total`, and
   `order.discount_code` are written from it, so a wrong number is persisted,
   not just displayed.
5. **Read the tests the PR adds, and ask what they avoid.** The new tests use
   12, 20, 60, and 3 units. None of them uses 10 or 50, which are the only two
   numbers the feature is named after. A test suite that steps around the
   advertised boundary is the strongest hint in the diff.

Questions worth asking the author, in the PR:

- What happens when a cart earns a tier and a fixed code that is larger than
  the order? Which one wins?
- Is `applied_codes` written anywhere, or only displayed?
- Why 12 and 60 units in the tests rather than 10 and 50?

## The reasoning chain for each finding

**The combined discount can exceed the subtotal (Blocker).**
`Discount.apply` ends with `if subtotal < off: return subtotal`, and
`volume_discount` ends with the same three lines. Two caps, each against the
full subtotal. Then `quote` does `discount = volume_off + code_off`. The moment
you see two independent caps feeding one addition, try to make both of them
bind: a cart small enough that the fixed code alone already hits the cap.
`Line("PEN", Money.of("0.40"), 12)` with FLAT5 gives a subtotal of 4.80, a
capped code amount of 4.80, a tier amount of 0.24, and a discount of 5.04. The
taxable amount goes negative, so does the total, and `OrderService.create`
stores it. This is the one that blocks merge: it is silent, it is money, and it
reaches the database.

**The tier boundary is exclusive (Major).**
`tier_for` reads `if quantity > tier.min_quantity`. Hold that against the
contract you wrote down in step 1: 10 units gets 5 percent. Ten is not greater
than ten. The same character costs the second tier too, since 50 units falls
through to the 5 percent row. The docstring above the table ("an order
qualifies for the largest tier its total unit count reaches") and the PR
description both say inclusive, so the code is alone in disagreeing. Note how
the ascending table plus last match wins is correct and deserves saying so; the
finding is one comparison, not the loop.

**The mutable default leaks codes between quotes (Major).**
`volume_codes: list[str] = []` in the signature, `volume_codes.append(...)` in
the body. Default arguments are evaluated once at import, so there is exactly
one list for the life of the process. Then check who passes it:
`PricingService.quote` calls `quote(lines, discounts, region)` with three
arguments, so every real call shares that list. The consequence is not an
exception, it is a slow smear: the first order of 10 or more units puts
VOLUME10 in the list, and every later order reports a tier it did not earn,
including in `order.discount_code`. The amounts stay right, which is what makes
it survive a manual test.

**Negative tier percentages pass validation (Minor).**
`__post_init__` checks `self.percent_off > 100` and nothing else. The author
was thinking about one end of the range. Ask what the other end does:
`VolumeTier(10, Decimal("-5"))` builds, `percent_down` returns -0.50, `quote`
adds it to the discount, and the total goes up. Nobody can enter a tier today
because the table is a constant, so this is a Minor, not a Major. If the PR's
own open question comes true and tiers move to the database, it becomes a
Blocker without anyone touching this line.

**The `percent_down` docstring contradicts the body (Nit).**
"rounded half up to cents" over a body that passes `ROUND_DOWN`. The give away
is `percent` two methods above with the identical sentence. Behavior is
correct; only the sentence is wrong. Say it in one line and move on.

**The unused rounding import (Nit).**
`ROUND_HALF_EVEN` is imported, listed in `__all__`, and never referenced. The
`__all__` entry is the only reason ruff does not flag it, which is worth
noticing on its own: a name in `__all__` is a promise to importers.

**The clean code you should not flag.**
`best_discount` was rewritten as
`max(discounts, key=lambda d: d.apply(subtotal), default=None)` while the
docstring still promises "ties go to the first one listed". `max` returns the
first maximal element, so ties still go to the first code, and
`test_best_discount_picks_largest_and_does_not_stack` asserts exactly that at
a 50.00 subtotal. Asserting that this flips tie order is a false positive.
Asking "does `max` keep the tie order the docstring promises?" is not, and it
is a fair question if you do not remember the rule.

## Five questions an interviewer would ask about the rewrite

1. The cap is one comparison after the addition. Where else could it have gone,
   and why is capping after adding better than applying the tier to the amount
   left after the code discount?
2. The mutable default was removed rather than repaired with
   `None` plus a copy. When would you keep the parameter, and what would you
   have to change in the callers to justify it?
3. `tier_for` returns the last matching tier because the table is ascending.
   What would you add to keep that assumption from breaking when someone
   inserts a tier in the wrong position?
4. Negative percentages now raise. Why raise rather than clamp to zero, and how
   would you decide that when the tiers come from an admin form instead of a
   constant?
5. The rewrite left `best_discount`, the floor rounding, and the shape of
   `VOLUME_TIERS` untouched. Which of the three is most likely to need a change
   next, and what would trigger it?
