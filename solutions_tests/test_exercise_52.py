"""Hidden tests for exercise 52: agent capacity and auto-assignment.

Each test below fails against ex/52-auto-assignment and passes against
solutions/52.
"""

from __future__ import annotations

import ast
import inspect
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from sandbox.helpdesk.assigner import AutoAssigner
from sandbox.helpdesk.db import Agent
from sandbox.helpdesk.domain.sla import Priority, has_capacity
from sandbox.helpdesk.metrics import QueueMetrics
from sandbox.helpdesk.repo import TicketRepo
from sandbox.helpdesk.service import NotAllowed, TicketService

ASSIGNER_PY = Path("sandbox/helpdesk/assigner.py")
SERVICE_PY = Path("sandbox/helpdesk/service.py")
SHIPPED_TEST_FILE = Path("sandbox/helpdesk/tests/test_auto_assignment.py")

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


# --- CC-01: claim's capacity check and write are not serialized, so two --
# --- concurrent claims at capacity minus one can both pass the check. ----


def test_claim_is_race_free_under_concurrent_capacity_checks(session_factory, db) -> None:
    agent = Agent(email="race@example.com", active=True, capacity=3)
    db.add(agent)
    db.commit()

    service = TicketService(session_factory)
    for i in range(agent.capacity - 1):
        warmup = service.create(f"warmup {i}", Priority.LOW, NOW)
        service.claim(agent.email, warmup.id, NOW)

    ticket_a = service.create("race a", Priority.LOW, NOW)
    ticket_b = service.create("race b", Priority.LOW, NOW)

    barrier = threading.Barrier(2, timeout=1.0)

    def _after_count() -> None:
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass

    original = TicketService._after_count
    TicketService._after_count = staticmethod(_after_count)
    try:
        results: list[str] = []

        def _claim(ticket_id: int) -> None:
            try:
                service.claim(agent.email, ticket_id, NOW)
                results.append("ok")
            except NotAllowed:
                results.append("refused")

        threads = [
            threading.Thread(target=_claim, args=(ticket_a.id,)),
            threading.Thread(target=_claim, args=(ticket_b.id,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
    finally:
        TicketService._after_count = original

    with session_factory() as check:
        final_open = TicketRepo(check).open_count_for_agent(agent.id)
    assert final_open == agent.capacity


# --- CC-03: the assigner used to keep one Session across iterations. -----


def test_assigner_holds_no_session_attribute_and_uses_unit_of_work(session_factory) -> None:
    metrics = QueueMetrics()
    assigner = AutoAssigner(session_factory, interval_seconds=30, metrics=metrics)
    assert not any(isinstance(v, Session) for v in vars(assigner).values())
    assert "unit_of_work" in inspect.getsource(assigner.run_once)


# --- CC-13: stop() used to only set a flag and never join the thread. ----


def test_stop_joins_the_thread_quickly_and_uses_an_event(session_factory) -> None:
    metrics = QueueMetrics()
    assigner = AutoAssigner(session_factory, interval_seconds=5, metrics=metrics)
    assigner.start()
    time.sleep(0.05)

    start = time.monotonic()
    assigner.stop()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert assigner._thread is not None
    assert not assigner._thread.is_alive()
    assert "Event" in ASSIGNER_PY.read_text()


# --- LG-12: has_capacity used <= instead of <, letting an agent at ------
# --- exactly capacity take one more ticket. -------------------------------


def test_has_capacity_boundary_at_exactly_capacity() -> None:
    assert has_capacity(4, 5) is True
    assert has_capacity(5, 5) is False


# --- DS-04: the assigner built its own QueueMetrics instead of taking ----
# --- the app's instance, so its assignments never showed in /metrics. ----


def test_assigner_accepts_and_uses_injected_metrics(session_factory, seeded) -> None:
    metrics = QueueMetrics()
    assigner = AutoAssigner(session_factory, interval_seconds=30, metrics=metrics)
    assert assigner.metrics is metrics

    service = TicketService(session_factory)
    service.create("needs an agent", Priority.LOW, NOW)
    assigner.run_once(NOW)

    assert metrics.snapshot()["assigned"] == 1


# --- DS-09: the assigner's iteration computed datetime.now(UTC) ----------
# --- internally instead of taking now, so it could not be tested with ---
# --- a fixed clock. --------------------------------------------------------


def test_run_once_has_a_now_parameter() -> None:
    sig = inspect.signature(AutoAssigner.run_once)
    assert "now" in sig.parameters


# --- DS-20: claim and assign each duplicated the same "count, check, ----
# --- write" block instead of sharing one helper. --------------------------


def test_claim_and_assign_share_one_capacity_helper() -> None:
    tree = ast.parse(SERVICE_PY.read_text())
    class_node = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TicketService"
    )
    methods = {n.name: n for n in class_node.body if isinstance(n, ast.FunctionDef)}
    private_helpers = [name for name in methods if name.startswith("_") and name != "__init__"]
    assert len(private_helpers) == 1
    helper = private_helpers[0]

    def _calls_helper(method_name: str) -> bool:
        calls = {
            node.func.attr
            for node in ast.walk(methods[method_name])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        return helper in calls

    assert _calls_helper("claim")
    assert _calls_helper("assign")


# --- TR-10: the shipped auto-assigner test started a thread and slept, --
# --- instead of driving one iteration directly. ---------------------------


def test_auto_assignment_test_file_does_not_sleep() -> None:
    assert "sleep" not in SHIPPED_TEST_FILE.read_text()
