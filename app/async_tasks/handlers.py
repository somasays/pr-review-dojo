"""Task handlers for the queue worker.

Handlers are plain sync callables; the worker runs them in a thread so the
event loop keeps polling.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.async_tasks.worker import QueueWorker
from app.db.session import get_engine
from app.services.export_service import DEFAULT_WINDOW_DAYS, ExportFilters, build_export

_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
_session = _factory()


def export_orders(payload: dict[str, Any]) -> dict[str, object]:
    """Build one page of a customer's order history for the export mailer."""
    end = datetime.now(tz=UTC)
    filters = ExportFilters(
        statuses=list(payload.get("statuses", ["paid", "shipped"])),
        start=end - timedelta(days=int(payload.get("days", DEFAULT_WINDOW_DAYS))),
        end=end,
        limit=int(payload.get("limit", 500)),
        cursor=payload.get("cursor"),
    )
    page = build_export(_session, int(payload["customer_id"]), filters)
    return {"rows": len(page.rows), "total": page.total, "next_cursor": page.next_cursor}


def register(worker: QueueWorker) -> None:
    worker.register("export_orders", export_orders)
