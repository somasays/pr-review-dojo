# Exercise 34 walkthrough: gift card redemption at checkout

Mode: teach. Domain: fastapi (with a domain-logic component). Difficulty: easy.

- Exercise PR: https://github.com/somasays/pr-review-dojo/pull/79
- Rewrite PR: https://github.com/somasays/pr-review-dojo/pull/81

Read the exercise PR with its inline comments first, then the rewrite PR
commit by commit, then this file.

## 1. Reading order for this diff

This feature has a shape you will see again: a pure rule at the bottom, a
small storage layer in the middle, one composer that wires them into an
existing workflow, and two thin HTTP endpoints on top. Read it bottom up.

1. **Read the PR description and the file list, not the code.** Nine files:
   a new domain module, a model and a migration, a repository addition, one
   service method, two schema additions, a new router, one line in the
   existing orders router, and two test files. The domain module and the
   repository are leaves with nothing under them, so they are the cheapest
   to verify and the right place to start.
2. **Open `app/domain/gift_card.py` first.** It is the whole feature's rule
   in nine lines of logic: given an order total and a card balance, how much
   comes off the card and what is left. No database, no FastAPI. Read the
   docstring's three promises (never redeem more than the balance, never
   leave the charge negative, never leave the balance negative) and check
   each one against the arithmetic below it, one at a time.
3. **Then `app/db/models.py` and the migration.** `GiftCard` is a new table
   with a `code`, a `balance`, and a `currency`. `Order` gains
   `gift_card_code` and `gift_card_redeemed`, plus a `remaining_charge`
   property. Confirm the property does what its name says.
4. **Then `app/db/repositories.py`, `GiftCardRepository`.** Two short
   methods: a lookup and a balance update. Nothing to trace yet, but note
   the lookup's shape, you will need it again in step 6.
5. **Then `app/services/order_service.py`.** This is the composer: it calls
   the domain rule and the repository, and it does so inside `create`, a
   method that already has an idempotency contract to keep (README
   convention 3). Read `_apply_gift_card` next to the rest of `create`, not
   in isolation, and ask where its work sits relative to the
   `begin_nested()` block a few lines below.
6. **Then `app/api/routers/gift_cards.py`.** Three lines. Compare its
   dependencies against `app/api/routers/customers.py` or `orders.py`:
   every handler in this codebase takes a principal. Does this one?
   Compare its lookup against the repository method you just read in step
   4: does it use it?
7. **Then the one-line change in `app/api/routers/orders.py`,
   `app/api/schemas.py`, and `app/api/main.py`.** Mechanical wiring, quick
   to confirm.
8. **Then the tests.** By now you know what the feature does, so you are
   only checking which inputs the shipped tests actually exercise.

## 2. What to grep for before commenting

- `CurrentPrincipal` or `Depends(get_principal` across `app/api/routers/`.
  Every existing handler has one. Count how many of `gift_cards.py`'s
  handlers do.
- `self.session.commit()` in `app/services/`. Convention 8 gives the
  transaction boundary to the caller; repositories and services flush, they
  do not commit. The one hit that is not in `app/api/deps.py` is the tell.
- `select(` in `app/api/routers/`. Should be zero; queries belong in
  repositories (README convention 8, `GiftCardRepository` exists for this
  one).
- `balance - ` and `redeemed` in `app/domain/gift_card.py`. Two quantities
  are derived from the same clamp; check which one the third value is
  subtracted from.
- `min_remaining` across the diff. If a parameter's name only appears at its
  own definition, nothing calls it that way.
- README conventions 3 (idempotent writes) and 8 (repositories never
  commit, response models are allowlists). This PR touches both.

## 3. The reasoning chain that surfaces each finding

**The unauthenticated balance lookup (Blocker).** Open every router in this
PR side by side with `customers.py`. `me`, `list_customers`,
`create_customer`, every order endpoint, all take a principal. `get_balance`
takes `code` and `db` and nothing else. That is the whole finding: a value
tied to a customer's money answers to anyone who can reach the process,
authenticated or not. The consequence is not abstract, either, a card code
is exactly the kind of short, guessable string a script can enumerate.

**The negative gift card balance (Major).** Read `redeem`'s docstring
promise: the balance never goes below zero. Now trace a partial redemption,
a fifty dollar order against a thirty dollar card. `redeemed` is correctly
capped at thirty, the smaller of the two. `remaining_charge` is correctly
twenty. `remaining_balance` is `balance - total`, thirty minus fifty,
negative ten. The formula only agrees with the docstring when the card
covers the whole order, which is also the only case any shipped test
exercises. Once you notice the third line subtracts a different quantity
than the first two use, the bug is the entire explanation.

**The redemption that survives a rollback it should not (Major).** Ask the
idempotency question the README forces on every write: what happens if this
runs twice, or if the surrounding transaction fails after this step runs
once? `_apply_gift_card` calls `self.session.commit()` right after debiting
the card, several lines before the order's own insert inside
`begin_nested()`. A commit ends the transaction right there. If the insert
below then loses the idempotency race and the caller rolls back, that
rollback cannot touch a commit that already happened. The stock decrement a
few lines later is protected by exactly this rollback; the gift card debit,
one step earlier in the same method, is not. Same method, same kind of
side effect, different transaction safety, that asymmetry is the finding.

## 4. The clean trap

`GiftCardRepository.get_by_code` is a four-line method that wraps one
`select`. Next to it, `get_balance` in the router inlines the same kind of
query by hand, which can make the repository method look like unnecessary
ceremony in comparison, why wrap a one-liner in a class just to call it from
one place? It is the same shape as `CustomerRepository.by_email`, already in
this codebase: repositories are the only place that builds queries, so the
method is what keeps every other module free of SQLAlchemy. Flagging the
router's inline query is correct; flagging the repository method as the
problem is a false positive.

## 5. Design and tests

Two more places a reviewer would comment past the defects, plus the test.

- **The loose primitives in `_apply_gift_card`** (`order_service.py`): the
  method takes `total_amount: Decimal` and `currency: str` instead of the
  `Money` the caller already built two lines above as `q.total`. It
  reassembles a `Money` on its first line, which means the amount and the
  currency travel separately for exactly as long as it takes for one of
  them to be passed in the wrong order by a future caller. Passing `q.total`
  removes both the seam and the reassembly.
- **The unused `min_remaining` parameter** (`gift_card.py`, `redeem`): grep
  for callers and there is exactly one, in `_apply_gift_card`, and it never
  passes it. A parameter with no caller is a promise ("a future feature can
  require the card keep a minimum balance") the code is carrying today for
  free, but it is not free: every reader of `redeem` has to understand what
  the keyword does before ruling it out as irrelevant to the bug they are
  chasing.
- **The missing partial-redemption test** (`tests/test_gift_card.py`): all
  three shipped cases redeem against a card whose balance is at or above the
  order total. A split like this is defined by the case where the two
  clamps disagree, balance smaller than total, and that is the one case
  none of the three touch. It is also exactly the input that trips the
  negative-balance defect above.

One interviewer question about them: the primitive-obsession fix and the
unused-parameter fix both remove something instead of adding it. What does
that suggest about which one to make first, and does fixing the signature
in `_apply_gift_card` change how obvious the missing `min_remaining` caller
is in `redeem`?

## 6. Questions worth asking the author

- Who is allowed to look up a gift card's balance? (This is the question
  that finds the Blocker without reading a line of the domain rule.)
- If the same card code is applied to two orders created moments apart, does
  the second one see the balance the first one left behind, or can they
  race? (Points straight at the transaction question in `_apply_gift_card`.)
- Is a gift card tied to a customer, or is it a bearer code redeemable by
  whoever has it, the way a physical gift card works? The current design
  answers "bearer code" by omission; is that the intended answer?
- What happens if the order this gift card is applied to is later
  cancelled? The PR does not restore the balance. Is that a follow-up, or
  is redemption meant to be final?
- The three shipped tests each cover one shape of `redeem`. Which one of the
  three most expensive failure modes, negative balance, double debit, or
  the missing auth, would any of them have caught?

## 7. Five interviewer questions about the rewrite

1. The fix for the negative balance changes one word, `total` to
   `redeemed`. Argue for and against the alternative rejected in the
   rewrite, clamping the result with `max(Money.zero(), balance - total)`.
   What class of future bug does the one-word fix prevent that the clamp
   does not?
2. The fix for the premature commit deletes three lines and adds none.
   What would a fix that kept the commit but tried to make it safe (a
   compensating rollback on later failure, for instance) have cost, and is
   that cost ever worth paying instead of just not committing early?
3. The rewrite lands the authentication fix and the design fix in the
   balance handler as two separate commits even though they touch adjacent
   lines. What is the argument for keeping them separate rather than one
   commit titled "fix the balance endpoint"?
4. `_apply_gift_card`'s signature changes from three primitives to one
   `Money`. Trace every call site this change affects. Is there a caller
   left anywhere in the codebase that still has to build a `Money` from
   parts just to call it, and if so, is that a sign the fix stopped one
   layer too early?
5. None of the seven fixes in the rewrite added a new class, a new module,
   or a new abstraction. Pick the one where an over-engineered version was
   most tempting and explain what it would have cost the next reader who
   has to change this feature.
