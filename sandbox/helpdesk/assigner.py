"""Background thread that assigns the oldest unassigned tickets to the
active agent with the most remaining capacity, once per interval."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from sandbox.helpdesk.domain.sla import TicketStatus, has_capacity
from sandbox.helpdesk.domain.sla import transition as transition_status
from sandbox.helpdesk.metrics import QueueMetrics
from sandbox.helpdesk.repo import AgentRepo, TicketRepo

BATCH_LIMIT = 20


class AutoAssigner:
    def __init__(self, session_factory: sessionmaker[Session], interval_seconds: float) -> None:
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self._session = session_factory()
        self._metrics = QueueMetrics()
        self._stopped = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="auto-assigner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped = True

    def _run(self) -> None:
        while not self._stopped:
            time.sleep(self.interval_seconds)
            self._assign_batch()

    def _assign_batch(self) -> None:
        now = datetime.now(UTC)
        tickets = TicketRepo(self._session)
        agents = AgentRepo(self._session)
        for ticket in tickets.unassigned_oldest_first(BATCH_LIMIT):
            best_agent = None
            best_remaining = -1
            for agent in agents.active():
                open_count = tickets.open_count_for_agent(agent.id)
                if not has_capacity(open_count, agent.capacity):
                    continue
                remaining = agent.capacity - open_count
                if remaining > best_remaining:
                    best_agent = agent
                    best_remaining = remaining
            if best_agent is None:
                continue
            ticket.agent_id = best_agent.id
            ticket.status = transition_status(TicketStatus.OPEN, TicketStatus.CLAIMED).value
            ticket.claimed_at = now
            self._metrics.record("assigned")
        self._session.commit()
