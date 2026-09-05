# Test review catalog

Findings about the test that ships with the PR. Every exercise includes a
plausible test written by the author; a strong reviewer reads it as
carefully as the code. These findings are graded from the review comment.
The reference fix on `solutions/N` improves the test, and that improved test
doubles as the hidden test when it would fail against the exercise branch.

## Findings

### TR-01: Happy path only
- Severity: Major
- Description: The test exercises the success case and never the branch the PR added for errors, limits, or edge input.
- Planting: A new endpoint gets one test that posts valid input and checks 201; the 409 and 422 branches, the empty list, and the cap are untested.
- Reference fix: add the missing cases; the risky one fails on the exercise branch when it also carries a defect there.

### TR-02: Asserts the mock, not the outcome
- Severity: Minor
- Description: The test checks that a collaborator was called with certain arguments and never checks the state or response that matters.
- Planting: `sender.send.assert_called_once()` with no assertion on the order status or the persisted row.
- Reference fix: assert on the response body and the repository state; keep the call assertion only if the call itself is the contract.

### TR-03: Boundary skipped
- Severity: Major
- Description: The code introduces a threshold (quantity 10, 200 dollars, 7 days) and the test uses values far from it.
- Planting: Threshold at 10, test uses 3 and 50.
- Reference fix: test 9, 10, and 11 (or the equivalent) and state the inclusive rule in the test name.

### TR-04: Duplicate of an existing test
- Severity: Minor
- Description: The new test re-checks behavior already covered in `tests/`, under a new name, and adds no new path.
- Planting: A second `test_create_order_happy_path` in a new file.
- Reference fix: delete it and extend the existing test or add the missing case instead.

### TR-05: Broad `pytest.raises`
- Severity: Minor
- Description: `pytest.raises(Exception)` accepts any failure, including a typo in the test.
- Planting: The PR adds a validation error and tests it with `pytest.raises(Exception)`.
- Reference fix: name the exception type and match the message.

### TR-06: Clock or order dependent
- Severity: Minor
- Description: The test depends on wall-clock time, on rows created by another test, or on dict ordering.
- Planting: `assert report["days"] == (date.today() - start).days` or a test that relies on the seeded fixture having been mutated by the previous test.
- Reference fix: pass a fixed `today`, build the rows the test needs.

### TR-07: Tests private details
- Severity: Minor
- Description: The test reaches into `_internal` attributes or patches a private helper, so any refactor breaks it without a behavior change.
- Planting: `assert service._cache == {...}` or `monkeypatch.setattr(module, "_helper", ...)`.
- Reference fix: assert through the public surface.

### TR-08: Fixture hides the defect
- Severity: Minor
- Description: The setup makes the failing branch unreachable: stock set to 1000 so insufficient stock never fires, a discount code that is not in the table, an empty queue.
- Planting: The test seeds a product with `stock=10_000` and then "tests" the stock check.
- Reference fix: seed the exact quantity that triggers the branch.

### TR-09: New public function untested
- Severity: Minor
- Description: A public function added by the PR has no test at all.
- Planting: The PR adds three public helpers; the test file mentions two.
- Reference fix: add one test per public function, minimal.

### TR-10: Sleep instead of a deterministic wait
- Severity: Minor
- Description: An async, thread, or streaming test uses `time.sleep` or `asyncio.sleep` to "let it finish" instead of awaiting the task, joining the thread, or using `availableNow`.
- Planting: `await asyncio.sleep(0.5); assert stats.processed == 3`.
- Reference fix: await the worker's completion, use an `Event`, or use the trigger that ends the query.

### TR-11: Name promises more than the assertion
- Severity: Minor
- Description: `test_rejects_negative_quantity` only checks the status code, or `test_idempotent` calls the endpoint once.
- Planting: A test named for a property that its body does not establish.
- Reference fix: make the assertion match the name, or rename.
