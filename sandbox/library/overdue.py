"""Overdue job: notify patrons of overdue loans, once per loan per day.

Owns its own transaction through db.session_scope(), the same as the API's
get_db dependency and for the same reason: this module and api.py are the
only two callers of session_scope() in this service.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from sandbox.library.db import Loan, session_scope
from sandbox.library.domain.lending import fine_for
from sandbox.library.repo import LoanRepo, PatronRepo
from sandbox.library.service import FINE_CAP, FINE_PER_DAY, GRACE_DAYS

Notifier = Callable[[str, Loan, Decimal], None]


def notify_overdue(session_factory: sessionmaker[Session], today: date, notifier: Notifier) -> int:
    """Call `notifier(email, loan, fine_so_far)` once for each overdue loan.

    A loan is due for notification when it is overdue as of `today` and its
    `last_notified_on` is not already `today`; running this job again later
    the same day is then a no-op for every loan it already touched.
    """
    notified = 0
    with session_scope(session_factory) as session:
        loans = LoanRepo(session)
        patrons = PatronRepo(session)
        for loan in loans.overdue(today):
            if loan.last_notified_on == today:
                continue
            patron = patrons.get(loan.patron_id)
            if patron is None:
                continue
            # A loan may already carry a fine frozen at its last renewal; the
            # amount owed is that frozen fine plus whatever has accrued
            # against the loan's current due date since then.
            fine_so_far = loan.frozen_fine + fine_for(
                loan.due_on, today, GRACE_DAYS, FINE_PER_DAY, FINE_CAP
            )
            notifier(patron.email, loan, fine_so_far)
            loan.last_notified_on = today
            notified += 1
    return notified
