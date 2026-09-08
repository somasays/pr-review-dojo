"""Reading submission and correction, and bill generation: business rules
layered on the repositories.

Each public method takes a `Session` opened elsewhere (the `get_db`
dependency in api.py, or a test fixture) and never commits it; the caller
owns the transaction through `db.session_scope()` (see
sandbox/metering/README.md).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from sandbox.metering.db import Bill, Reading, ensure_aware_utc
from sandbox.metering.domain.tariff import InvalidReading as DomainInvalidReading
from sandbox.metering.domain.tariff import (
    Tariff,
    adjustment_amount,
    charge_for,
    consumption,
    period_days,
)
from sandbox.metering.repo import AccountRepo, BillRepo, MeterRepo, ReadingRepo


class NotFound(Exception):
    pass


class NotAllowed(Exception):
    pass


class InvalidReading(Exception):
    pass


class ReadingService:
    """`reader_email` is the reader's identity, kept for the caller's own
    audit trail; it is not looked up against `Account`, because a reader
    is an operational role authorized by the X-Metering-Key reader key,
    not a billed customer."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.meters = MeterRepo(session)
        self.readings = ReadingRepo(session)

    def submit(
        self,
        reader_email: str,
        meter_serial: str,
        taken_at: datetime,
        value_kwh: Decimal,
        source: str,
    ) -> Reading:
        """Append a reading for `meter_serial`, validated against the
        latest earlier reading (see domain.tariff.consumption)."""
        ensure_aware_utc(taken_at)
        del reader_email  # see class docstring

        meter = self.meters.by_serial(meter_serial)
        if meter is None:
            raise NotFound(f"meter {meter_serial!r} not found")

        previous = self.readings.latest_before(meter.id, taken_at)
        if previous is not None:
            try:
                consumption(previous.value_kwh, value_kwh, meter.max_reading)
            except DomainInvalidReading as exc:
                raise InvalidReading(str(exc)) from exc

        reading = Reading(meter_id=meter.id, taken_at=taken_at, value_kwh=value_kwh, source=source)
        return self.readings.add(reading)

    def correct(self, reader_email: str, reading_id: int, value_kwh: Decimal) -> Reading:
        """Append a new row that supersedes `reading_id`; the superseded
        row is never updated or deleted (see sandbox/metering/README.md).
        The value is not re-validated: a correction exists precisely
        because the original check might have been wrong. Correcting an
        already-superseded reading is not allowed."""
        del reader_email  # see class docstring

        original = self.readings.get(reading_id)
        if original is None:
            raise NotFound(f"reading {reading_id} not found")
        if self.readings.is_superseded(reading_id):
            raise NotAllowed(f"reading {reading_id} has already been superseded")

        replacement = Reading(
            meter_id=original.meter_id,
            taken_at=original.taken_at,
            value_kwh=value_kwh,
            source=original.source,
        )
        return self.readings.supersede(reading_id, replacement)


class BillingService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.accounts = AccountRepo(session)
        self.meters = MeterRepo(session)
        self.readings = ReadingRepo(session)
        self.bills = BillRepo(session)

    def generate(
        self, account_email: str, period_start: date, period_end: date, tariff: Tariff
    ) -> Bill:
        """Generate the bill for one half-open period `[period_start,
        period_end)`. Idempotent per (account, period): calling this
        twice for the same period returns the existing Bill. For each
        meter, consumption is the delta between the latest reading at or
        before the period's start and at or before its end; a meter with
        fewer than two such readings contributes nothing."""
        if period_end <= period_start:
            raise InvalidReading("period_end must be after period_start")

        account = self.accounts.by_email(account_email)
        if account is None or not account.active:
            raise NotFound(f"account {account_email!r} not found or inactive")

        existing = self.bills.for_period(account.id, period_start, period_end)
        if existing is not None:
            return existing

        start_at = datetime.combine(period_start, datetime.min.time(), tzinfo=UTC)
        end_at = datetime.combine(period_end, datetime.min.time(), tzinfo=UTC)

        total_kwh = Decimal("0.000")
        for meter in self.meters.for_account(account.id):
            opening = self.readings.latest_at_or_before(meter.id, start_at)
            closing = self.readings.latest_at_or_before(meter.id, end_at)
            if opening is None or closing is None or closing.id == opening.id:
                continue
            try:
                total_kwh += consumption(opening.value_kwh, closing.value_kwh, meter.max_reading)
            except DomainInvalidReading as exc:
                raise InvalidReading(str(exc)) from exc

        days = period_days(period_start, period_end)
        amount = charge_for(total_kwh, tariff, days)

        bill = Bill(
            account_id=account.id,
            period_start=period_start,
            period_end=period_end,
            kwh=total_kwh,
            amount=amount,
            generated_at=datetime.now(UTC),
        )
        return self.bills.add(bill)

    def apply_correction(self, reader_email: str, reading_id: int, tariff: Tariff) -> list[Bill]:
        """Recompute every bill whose period covers the correction
        `reading_id` and append a new bill for the difference, pointing
        `adjusts_bill_id` at the original. The original bill is never
        modified."""
        del reader_email

        corrected = self.readings.get(reading_id)
        if corrected is None:
            raise NotFound(f"reading {reading_id} not found")
        if corrected.supersedes_id is None:
            raise NotAllowed(f"reading {reading_id} is not a correction")

        meter = self.meters.get(corrected.meter_id)
        if meter is None:
            raise NotFound(f"meter {corrected.meter_id} not found")
        account = self.accounts.get(meter.account_id)
        if account is None:
            raise NotFound(f"account {meter.account_id} not found")

        issued: list[Bill] = []
        for original in self.bills.affected_by(account.id, corrected.taken_at):
            recomputed_kwh = self._kwh_for_period(
                account.id, original.period_start, original.period_end
            )
            days = period_days(original.period_start, original.period_end)
            recomputed_amount = charge_for(recomputed_kwh, tariff, days)
            delta = adjustment_amount(original.amount, recomputed_amount)
            if delta == Decimal("0.00"):
                continue

            adjustment = self.bills.add(
                Bill(
                    account_id=account.id,
                    period_start=original.period_start,
                    period_end=original.period_end,
                    kwh=recomputed_kwh - original.kwh,
                    amount=delta,
                    generated_at=datetime.now(UTC),
                    adjusts_bill_id=original.id,
                    correction_reading_id=corrected.id,
                )
            )
            issued.append(adjustment)
        return issued

    def _kwh_for_period(self, account_id: int, period_start: date, period_end: date) -> Decimal:
        """Total consumption for account_id over [period_start, period_end)."""
        start_at = datetime.combine(period_start, datetime.min.time(), tzinfo=UTC)
        end_at = datetime.combine(period_end, datetime.min.time(), tzinfo=UTC)
        total_kwh = Decimal("0.000")
        for meter in self.meters.for_account(account_id):
            opening = self.readings.latest_at_or_before(meter.id, start_at)
            closing = self.readings.latest_at_or_before(meter.id, end_at)
            if opening is None or closing is None or closing.id == opening.id:
                continue
            try:
                total_kwh += consumption(opening.value_kwh, closing.value_kwh, meter.max_reading)
            except DomainInvalidReading as exc:
                raise InvalidReading(str(exc)) from exc
        return total_kwh
