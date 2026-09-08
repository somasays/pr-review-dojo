"""Reuse the sandbox/metering fixtures for the exercise 49 hidden tests."""

from sandbox.metering.tests.conftest import (  # noqa: F401
    CUSTOMER_EMAIL,
    CUSTOMER_KEY,
    EPOCH,
    METER_SERIAL,
    READER_EMAIL,
    READER_KEY,
    TARIFF,
    client,
    db,
    engine,
    seeded,
    session_factory,
)
